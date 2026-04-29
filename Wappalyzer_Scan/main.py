import argparse
import os
import sys

from config import RunConfig
from logger import setup_logger
from pipeline import run


def parse_args() -> RunConfig:
    p = argparse.ArgumentParser(description="Wappalyzer technology scanner")
    p.add_argument("--input", required=True, help="File with one domain per line")
    p.add_argument("--output-json", default="results.json")
    p.add_argument("--output-csv", default="results.csv")
    p.add_argument("--state", default="state.json")
    p.add_argument("--api-key", default=os.environ.get("WAPPALYZER_API_KEY"))
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--max-retries", type=int, default=5)
    p.add_argument("--sets", default="all")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--skip-credits-check", action="store_true")
    args = p.parse_args()

    if not args.api_key:
        print("ERROR: API key required (--api-key or WAPPALYZER_API_KEY env).", file=sys.stderr)
        sys.exit(2)

    return RunConfig(
        api_key=args.api_key,
        input_path=args.input,
        output_json=args.output_json,
        output_csv=args.output_csv,
        state_path=args.state,
        batch_size=min(10, max(1, args.batch_size)),
        max_retries=args.max_retries,
        sets=args.sets,
        recursive=not args.no_recursive,
        skip_credits_check=args.skip_credits_check,
    )


def main() -> None:
    cfg = parse_args()
    logger = setup_logger()
    try:
        run(cfg, logger)
    except KeyboardInterrupt:
        print("\nInterrupted. State saved — re-run with same --state to resume.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
