from dataclasses import dataclass

API_BASE = "https://api.wappalyzer.com/v2"
LOOKUP_ENDPOINT = f"{API_BASE}/lookup/"
CREDITS_ENDPOINT = f"{API_BASE}/credits/balance/"

MAX_URLS_PER_REQUEST = 10        # per docs
RATE_LIMIT_PER_SECOND = 10        # per docs
DEFAULT_TIMEOUT = 30              # per docs
DEFAULT_MAX_RETRIES = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 60.0
CRAWL_REPOLL_ATTEMPTS = 3         # per docs recommendation
CRAWL_REPOLL_DELAY = 5 * 60       # 5 minutes per docs

@dataclass
class RunConfig:
    api_key: str
    input_path: str
    output_json: str
    output_csv: str
    state_path: str
    batch_size: int = MAX_URLS_PER_REQUEST
    max_retries: int = DEFAULT_MAX_RETRIES
    sets: str = "all"
    recursive: bool = True
    skip_credits_check: bool = False
