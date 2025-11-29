#!/usr/bin/env python3
"""Generate redacted copies of MCP financial analyzer trace logs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
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
    (re.compile(r"Bearer\\s+[A-Za-z0-9._-]+", re.IGNORECASE), "Bearer REDACTED"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\\s\\S]+?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "-----BEGIN PRIVATE KEY-----REDACTED-----END PRIVATE KEY-----",
    ),
)


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


def sanitize_logs(source_dir: Path, dest_dir: Path) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)

    for path in sorted(source_dir.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source_dir)
        dest_file = dest_dir / relative
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
    sanitize_logs(source_dir, dest_dir)


if __name__ == "__main__":
    main()
