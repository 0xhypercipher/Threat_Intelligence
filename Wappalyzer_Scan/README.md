### File Structure

wappalyzer_scan/
├── main.py                  # CLI entry point
├── config.py                # Constants, defaults
├── api_client.py            # WappalyzerClient (HTTP, auth, rate limit, retry)
├── rate_limiter.py          # Token-bucket throttle
├── pipeline.py              # Orchestration: batching, state, retries, crawl repolls
├── transformer.py           # JSON → CSV row generator + normalization
├── state.py                 # Resume state I/O
├── logger.py                # Logging setup
└── requirements.txt

### Module Responsibilities

- **api_client.py**: `lookup(urls: list[str]) -> list[dict]`, `credits() -> int`. Handles auth header, retries, backoff, header parsing.
- **rate_limiter.py**: thread-safe token bucket (10/sec).
- **pipeline.py**: drives the full run; handles batching, crawl re-polling, state updates, progress bar.
- **transformer.py**: emits CSV rows per spec (DMARC, SPF, SSL normalization).
- **state.py**: load/save resume state atomically.
- **main.py**: arg parsing, glue.

### How to Run

`pip install -r requirements.txt
export WAPPALYZER_API_KEY="your_key_here"
python main.py \
  --input domains.txt \
  --output-json results.json \
  --output-csv results.csv \
  --state state.json`

  ### Key Features

- **No interruption** — every per-domain failure is caught, logged, and recorded; pipeline continues.
- **Documented rate limit obeyed** — token bucket capped at 10 req/s; batches of 10 URLs.
- **HTTP 429** — exponential backoff with jitter, honoring `Retry-After`.
- **Crawl re-polling** — 3 attempts × 5 min, exactly per docs recommendation.
- **Resume** — state saved after every batch.
- **Atomic writes** — temp file + rename for JSON/CSV/state.
- **CSV format** — strictly matches the example (id+domain on first row, blanks on continuation rows; tech+version+result rows; dmarc/spf/ssl synthetic rows with normalized values).
