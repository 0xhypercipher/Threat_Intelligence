import csv
from datetime import datetime, timezone
from typing import Iterable


def _normalize_bool_flag(value) -> str:
    if value is True:
        return "enabled"
    if value is False:
        return "missing"
    return "unknown"


def _format_ssl(cert_valid_to) -> str:
    if cert_valid_to in (None, "", 0):
        return "unknown"
    try:
        ts = int(cert_valid_to)
    except (TypeError, ValueError):
        return "unknown"
    expiry = datetime.fromtimestamp(ts, tz=timezone.utc).date()
    today = datetime.now(tz=timezone.utc).date()
    iso = expiry.isoformat()
    return f"expired at {iso}" if expiry < today else f"valid until {iso}"


def envelope_to_rows(envelope: dict, row_id: int) -> Iterable[list]:
    """
    Yields CSV rows for a single domain envelope.
    First row carries id+domain; subsequent rows leave them blank.
    """
    domain = envelope.get("domain", "")
    response = envelope.get("response") or {}
    technologies = response.get("technologies") or []

    first_emitted = False

    def emit(tech: str, version: str, result: str):
        nonlocal first_emitted
        if not first_emitted:
            yield [row_id, domain, tech, version, result]
            first_emitted = True
        else:
            yield ["", "", tech, version, result]

    # Technologies
    if technologies:
        for t in technologies:
            slug = (t.get("slug") or t.get("name") or "").strip()
            if not slug:
                continue
            versions = t.get("versions") or []
            version = versions[0] if versions else ""
            yield from emit(slug, version, "")

    # DMARC
    yield from emit("dmarc", "", _normalize_bool_flag(response.get("dns.dmarc")))

    # SPF
    yield from emit("spf", "", _normalize_bool_flag(response.get("dns.spf")))

    # SSL
    yield from emit("ssl", "", _format_ssl(response.get("certInfo.validTo")))

    # If absolutely nothing emitted (extremely defensive), still write a marker
    if not first_emitted:
        yield [row_id, domain, "", "", "no_data"]


def write_csv(envelopes: list[dict], output_path: str) -> int:
    rows_written = 0
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "domain", "technology", "version", "result"])
        for idx, env in enumerate(envelopes, start=1):
            for row in envelope_to_rows(env, idx):
                writer.writerow(row)
                rows_written += 1
    return rows_written
