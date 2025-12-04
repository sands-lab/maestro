#!/usr/bin/env python3
"""Generate redacted copies of MCP financial analyzer trace logs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Iterable


REDACTED_VALUE = "***REDACTED***"
SECRET_KEYS = {
    "api_key",
    "apikey",
    "openai_api_key",
    "google_api_key",
    "tavily_api_key",
    "authorization",
    "auth_header",
    "bearer_token",
    "private_key",
}
SECRET_KEY_SUBSTRINGS = ("password",)
PATTERN_REPLACEMENTS: Iterable[tuple[re.Pattern[str], str]] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{10,}"), "sk-REDACTED"),
    (re.compile(r"AIza[0-9A-Za-z_-]{10,}"), "AIzaREDACTED"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE), "Bearer REDACTED"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "-----BEGIN PRIVATE KEY-----REDACTED-----END PRIVATE KEY-----",
    ),
)


LOG_TIMESTAMP_PATTERN = re.compile(r"(\d{8}_\d{6})")
FILTER_FORMATS = ("%Y%m%d", "%Y%m%d_%H%M%S")


def parse_filter_datetime(value: str | None, *, inclusive_end: bool = False) -> datetime | None:
    """Convert CLI date filters into datetime objects."""
    if value is None:
        return None
    for fmt in FILTER_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y%m%d" and inclusive_end:
                # Treat day-only end filters as inclusive through the end of the day.
                return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            return parsed
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Invalid date '{value}'. Expected formats: YYYYMMDD or YYYYMMDD_HHMMSS"
    )


def extract_timestamp_from_name(path: Path) -> datetime | None:
    """Return datetime embedded inside known log filenames."""
    match = LOG_TIMESTAMP_PATTERN.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def should_include_file(path: Path, start: datetime | None, end: datetime | None) -> bool:
    """Decide whether a file should be sanitized based on requested date filters."""
    if start is None and end is None:
        return True
    timestamp = extract_timestamp_from_name(path)
    if timestamp is None:
        return False
    if start and timestamp < start:
        return False
    if end and timestamp > end:
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy trace logs to a new directory with sensitive data removed."
    )
    parser.add_argument(
        "--source",
        default="logs",
        help="Directory containing raw trace files (default: logs)",
    )
    parser.add_argument(
        "--dest",
        default="logs_clean",
        help="Directory to write sanitized copies (default: logs_clean)",
    )
    parser.add_argument(
        "--start-date",
        type=parse_filter_datetime,
        help="Only sanitize files whose timestamp is on/after this date (YYYYMMDD or YYYYMMDD_HHMMSS)",
    )
    parser.add_argument(
        "--end-date",
        type=partial(parse_filter_datetime, inclusive_end=True),
        help="Only sanitize files whose timestamp is on/before this date (YYYYMMDD or YYYYMMDD_HHMMSS)",
    )
    return parser.parse_args()


def scrub_string(value: str) -> str:
    """Redact secrets embedded inside large strings."""
    redacted = value
    for pattern, replacement in PATTERN_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def should_redact_key(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in SECRET_KEYS:
        return True
    return any(sub in key_lower for sub in SECRET_KEY_SUBSTRINGS)


def scrub_obj(obj: Any) -> Any:
    """Recursively scrub sensitive data from parsed JSON."""
    if isinstance(obj, dict):
        return {
            key: (REDACTED_VALUE if should_redact_key(key) else scrub_obj(value))
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [scrub_obj(item) for item in obj]
    if isinstance(obj, str):
        return scrub_string(obj)
    return obj


def sanitize_json_line(line: str) -> str:
    """Redact a single JSON object that occupies one line."""
    stripped = line.strip()
    if not stripped:
        return stripped
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return scrub_string(stripped)
    scrubbed = scrub_obj(parsed)
    return json.dumps(scrubbed, ensure_ascii=False)


def sanitize_json_file(src: Path, dest: Path) -> None:
    parsed = json.loads(src.read_text())
    scrubbed = scrub_obj(parsed)
    dest.write_text(json.dumps(scrubbed, indent=2, ensure_ascii=False) + "\n")


def sanitize_logs(
    source_dir: Path,
    dest_dir: Path,
    *,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)

    for path in sorted(source_dir.rglob("*")):
        if path.is_dir():
            continue
        if not should_include_file(path, start_date, end_date):
            continue
        relative = path.relative_to(source_dir)
        dest_file = dest_dir / relative
        if dest_file.exists():
            # Skip files we've already sanitized to keep destination append-only.
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if path.suffix == ".jsonl":
            with path.open("r") as src_handle, dest_file.open("w") as dest_handle:
                for line in src_handle:
                    dest_handle.write(sanitize_json_line(line) + "\n")
        elif path.suffix == ".json":
            sanitize_json_file(path, dest_file)
        else:
            # Fallback: copy bytes without touching contents.
            shutil.copy2(path, dest_file)


def main() -> None:
    args = parse_args()
    source_dir = Path(args.source).resolve()
    dest_dir = Path(args.dest).resolve()

    if not source_dir.exists():
        raise SystemExit(f"Source directory not found: {source_dir}")
    sanitize_logs(
        source_dir,
        dest_dir,
        start_date=args.start_date,
        end_date=args.end_date,
    )


if __name__ == "__main__":
    main()
