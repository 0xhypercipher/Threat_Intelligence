import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from tqdm import tqdm

from api_client import WappalyzerClient, WappalyzerAuthError
from config import (
    RunConfig, MAX_URLS_PER_REQUEST,
    CRAWL_REPOLL_ATTEMPTS, CRAWL_REPOLL_DELAY,
)
from state import atomic_write, load_state, save_state
from transformer import write_csv


def normalize_domain(raw: str) -> str | None:
    raw = raw.strip().lower()
    if not raw or raw.startswith("#"):
        return None
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.netloc or parsed.path
    else:
        host = raw
    host = host.split("/")[0].strip()
    if not host or "." not in host:
        return None
    return host


def domain_to_url(domain: str) -> str:
    return f"https://{domain}"


def url_to_domain(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.netloc or parsed.path).split("/")[0].lower()


def load_domains(path: str) -> list[str]:
    seen, out = set(), []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            d = normalize_domain(line)
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def _is_crawl_pending(item: dict) -> bool:
    return bool(item.get("crawl")) and not (item.get("technologies") or [])


def _build_envelope(domain: str, item: dict, status: str, attempts: int, error: str | None = None) -> dict:
    return {
        "domain": domain,
        "url": domain_to_url(domain),
        "status": status,
        "attempts": attempts,
        "error": error,
        "response": item,
    }


def run(cfg: RunConfig, logger) -> None:
    started = datetime.now(timezone.utc).isoformat()
    domains = load_domains(cfg.input_path)
    if not domains:
        logger.error("No valid domains found in %s", cfg.input_path)
        return

    logger.info("Loaded %d unique domains", len(domains))

    client = WappalyzerClient(cfg.api_key, logger=logger, max_retries=cfg.max_retries)

    # Pre-flight credits
    if not cfg.skip_credits_check:
        try:
            balance = client.credits()
            logger.info("Credits available: %d (need ~%d)", balance, len(domains))
            if balance < len(domains):
                logger.warning(
                    "Credit balance (%d) is lower than domain count (%d). Continuing anyway.",
                    balance, len(domains),
                )
        except Exception as e:
            logger.warning("Credit pre-flight check failed (non-fatal): %s", e)

    # Resume state
    state = load_state(cfg.state_path)
    completed: dict[str, dict] = state.get("completed", {})
    pending_crawls: dict[str, dict] = state.get("pending_crawls", {})

    todo = [d for d in domains if d not in completed]
    logger.info("To process: %d (already completed: %d)", len(todo), len(completed))

    # ---------- main batched loop ----------
    pbar = tqdm(total=len(domains), initial=len(completed), desc="Lookup", unit="domain")

    try:
        for i in range(0, len(todo), cfg.batch_size):
            batch = todo[i : i + cfg.batch_size]
            urls = [domain_to_url(d) for d in batch]

            try:
                results = client.lookup(urls, sets=cfg.sets, recursive=cfg.recursive)
            except WappalyzerAuthError as e:
                logger.error("Aborting run: %s", e)
                break
            except Exception as e:
                logger.error("Batch %s failed unrecoverably: %s", batch, e)
                for d in batch:
                    completed[d] = _build_envelope(d, {}, "failed", cfg.max_retries, str(e))
                pbar.update(len(batch))
                save_state(cfg.state_path, {"completed": completed, "pending_crawls": pending_crawls})
                continue

            # Map results back to domains by URL
            results_by_domain = {url_to_domain(r.get("url", "")): r for r in results if isinstance(r, dict) and r.get("url")}
            # Handle synthetic batch-wide errors (e.g., 400)
            batch_error = next((r for r in results if isinstance(r, dict) and "_http_error" in r), None)

            for d in batch:
                item = results_by_domain.get(d)
                if item is None:
                    if batch_error:
                        completed[d] = _build_envelope(
                            d, {}, "failed", 1,
                            f"HTTP {batch_error['_http_error']}: {batch_error.get('_message','')}",
                        )
                    else:
                        completed[d] = _build_envelope(d, {}, "failed", 1, "No response item returned")
                    continue

                if item.get("errors"):
                    completed[d] = _build_envelope(d, item, "failed", 1, "; ".join(map(str, item["errors"])))
                    continue

                if _is_crawl_pending(item):
                    pending_crawls[d] = {
                        "attempts": pending_crawls.get(d, {}).get("attempts", 0) + 1,
                        "next_at": time.time() + CRAWL_REPOLL_DELAY,
                        "last_response": item,
                    }
                    continue

                completed[d] = _build_envelope(d, item, "ok", 1)

            pbar.update(len(batch))
            pbar.set_postfix(
                ok=sum(1 for v in completed.values() if v["status"] == "ok"),
                failed=sum(1 for v in completed.values() if v["status"] == "failed"),
                pending=len(pending_crawls),
                credits=client.credits_remaining,
            )

            save_state(cfg.state_path, {"completed": completed, "pending_crawls": pending_crawls})

        pbar.close()

        # ---------- crawl re-poll phase ----------
        if pending_crawls:
            logger.info("Re-polling %d pending crawls", len(pending_crawls))
            for round_idx in range(CRAWL_REPOLL_ATTEMPTS):
                if not pending_crawls:
                    break
                # Wait until earliest next_at
                wait = max(0, min(p["next_at"] for p in pending_crawls.values()) - time.time())
                if wait > 0:
                    logger.info("Waiting %.0fs before re-poll round %d", wait, round_idx + 1)
                    time.sleep(wait)

                still_pending: dict[str, dict] = {}
                pending_domains = list(pending_crawls.keys())
                for i in range(0, len(pending_domains), cfg.batch_size):
                    batch = pending_domains[i : i + cfg.batch_size]
                    urls = [domain_to_url(d) for d in batch]
                    try:
                        results = client.lookup(urls, sets=cfg.sets, recursive=cfg.recursive)
                    except WappalyzerAuthError as e:
                        logger.error("Aborting re-poll: %s", e)
                        break
                    except Exception as e:
                        logger.error("Re-poll batch failed: %s", e)
                        for d in batch:
                            still_pending[d] = pending_crawls[d]
                        continue

                    results_by_domain = {
                        url_to_domain(r.get("url", "")): r
                        for r in results if isinstance(r, dict) and r.get("url")
                    }
                    for d in batch:
                        item = results_by_domain.get(d, {})
                        if _is_crawl_pending(item):
                            entry = pending_crawls[d]
                            entry["attempts"] = entry.get("attempts", 0) + 1
                            entry["next_at"] = time.time() + CRAWL_REPOLL_DELAY
                            still_pending[d] = entry
                        elif item.get("errors"):
                            completed[d] = _build_envelope(d, item, "failed", round_idx + 2, "; ".join(map(str, item["errors"])))
                        elif item:
                            completed[d] = _build_envelope(d, item, "ok", round_idx + 2)
                        else:
                            still_pending[d] = pending_crawls[d]

                pending_crawls = still_pending
                save_state(cfg.state_path, {"completed": completed, "pending_crawls": pending_crawls})

            # Anything still pending → mark crawl_pending
            for d, entry in pending_crawls.items():
                completed[d] = _build_envelope(
                    d, entry.get("last_response", {}), "crawl_pending",
                    entry.get("attempts", 0), "crawl did not complete in time",
                )
            pending_crawls = {}
            save_state(cfg.state_path, {"completed": completed, "pending_crawls": pending_crawls})

    except KeyboardInterrupt:
        logger.warning("Interrupted by user — saving state.")
        save_state(cfg.state_path, {"completed": completed, "pending_crawls": pending_crawls})
        raise

    # ---------- assemble outputs ----------
    finished = datetime.now(timezone.utc).isoformat()
    envelopes = [completed[d] for d in domains if d in completed]

    succeeded = sum(1 for e in envelopes if e["status"] == "ok")
    failed = sum(1 for e in envelopes if e["status"] == "failed")
    pending = sum(1 for e in envelopes if e["status"] == "crawl_pending")

    output = {
        "metadata": {
            "started_at": started,
            "finished_at": finished,
            "total": len(domains),
            "succeeded": succeeded,
            "failed": failed,
            "crawl_pending": pending,
            "credits_remaining": client.credits_remaining,
        },
        "results": envelopes,
    }
    atomic_write(cfg.output_json, json.dumps(output, ensure_ascii=False, indent=2))
    rows = write_csv(envelopes, cfg.output_csv)

    # ---------- summary ----------
    print()
    print("=" * 60)
    print("Run finished")
    print(f"Total domains:        {len(domains)}")
    print(f"Successful:           {succeeded}")
    print(f"Failed:               {failed}")
    print(f"Crawl pending:        {pending}")
    print(f"Credits remaining:    {client.credits_remaining}")
    print(f"CSV rows written:     {rows}")
    print(f"JSON written: {cfg.output_json}")
    print(f"CSV  written: {cfg.output_csv}")
    print("=" * 60)
