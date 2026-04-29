import random
import time
from typing import Any

import requests

from config import (
    LOOKUP_ENDPOINT, CREDITS_ENDPOINT, DEFAULT_TIMEOUT,
    BACKOFF_BASE, BACKOFF_CAP, RATE_LIMIT_PER_SECOND, MAX_URLS_PER_REQUEST,
)
from rate_limiter import TokenBucket


class WappalyzerAuthError(Exception):
    """403 - hard stop."""


class WappalyzerClient:
    def __init__(self, api_key: str, logger, max_retries: int = 5):
        self.api_key = api_key
        self.logger = logger
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key})
        self.bucket = TokenBucket(RATE_LIMIT_PER_SECOND, RATE_LIMIT_PER_SECOND)
        self.credits_remaining: int | None = None

    # ---------- public ----------

    def credits(self) -> int:
        self.bucket.acquire()
        r = self.session.get(CREDITS_ENDPOINT, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return int(r.json().get("credits", 0))

    def lookup(self, urls: list[str], sets: str = "all", recursive: bool = True) -> list[dict]:
        if not urls:
            return []
        if len(urls) > MAX_URLS_PER_REQUEST:
            raise ValueError(f"Cannot exceed {MAX_URLS_PER_REQUEST} urls per request")

        params = {
            "urls": ",".join(urls),
            "sets": sets,
            "recursive": "true" if recursive else "false",
        }
        return self._request_with_retry(params)

    # ---------- internal ----------

    def _request_with_retry(self, params: dict) -> list[dict]:
        attempt = 0
        while True:
            attempt += 1
            self.bucket.acquire()
            try:
                resp = self.session.get(
                    LOOKUP_ENDPOINT, params=params, timeout=DEFAULT_TIMEOUT
                )
            except requests.RequestException as e:
                self.logger.warning("Network error (attempt %d): %s", attempt, e)
                if attempt > self.max_retries:
                    raise
                self._backoff(attempt)
                continue

            self._update_credits(resp)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    self.logger.error("Malformed JSON from API: %s", resp.text[:200])
                    raise
                if isinstance(data, dict):
                    data = [data]
                return data

            if resp.status_code == 403:
                self.logger.error("403 from API: %s", resp.text[:300])
                raise WappalyzerAuthError(
                    "Wappalyzer 403 — invalid API key, wrong plan, or insufficient credits."
                )

            if resp.status_code == 400:
                self.logger.error("400 Bad Request — not retrying. Body: %s", resp.text[:300])
                # Return a synthetic error envelope so the caller can mark domains failed
                return [{"_http_error": 400, "_message": resp.text[:300]}]

            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp)
                self.logger.warning(
                    "429 rate limited (attempt %d). Sleeping %.1fs", attempt, retry_after
                )
                if attempt > self.max_retries:
                    raise RuntimeError("Rate limit retries exhausted")
                time.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600:
                self.logger.warning(
                    "5xx (attempt %d): %s", attempt, resp.status_code
                )
                if attempt > self.max_retries:
                    raise RuntimeError(f"Server error {resp.status_code} retries exhausted")
                self._backoff(attempt)
                continue

            # Unknown non-2xx: best-effort retry
            self.logger.warning("Unexpected status %s: %s", resp.status_code, resp.text[:200])
            if attempt > self.max_retries:
                return [{"_http_error": resp.status_code, "_message": resp.text[:300]}]
            self._backoff(attempt)

    def _update_credits(self, resp: requests.Response) -> None:
        rem = resp.headers.get("wappalyzer-credits-remaining")
        if rem is not None:
            try:
                self.credits_remaining = int(rem)
            except ValueError:
                pass

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
        return 2.0  # safe default

    @staticmethod
    def _backoff(attempt: int) -> None:
        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1)))
        delay = delay * (0.5 + random.random())  # jitter
        time.sleep(delay)
