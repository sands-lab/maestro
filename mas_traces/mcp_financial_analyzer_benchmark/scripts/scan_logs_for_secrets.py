#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path


PATTERNS = {
    "openai": re.compile(r"sk-[A-Za-z0-9]{16,}"),
    "google": re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    "aws": re.compile(r"AKIA[0-9A-Z]{16}"),
    "bearer": re.compile(r"Bearer [A-Za-z0-9_\-.]{20,}"),
}

API_KEY_SAFE_TOKENS = ("REDACTED", "None", "null", '""', "''")


def scan_file(path: Path):
    suspicious = []
    unsanitized = []
    try:
        with path.open("r", errors="ignore") as handle:
            for lineno, line in enumerate(handle, 1):
                if "api_key" in line and not any(tok in line for tok in API_KEY_SAFE_TOKENS):
                    unsanitized.append((lineno, line.strip()))
                for label, pattern in PATTERNS.items():
                    for match in pattern.finditer(line):
                        token = match.group(0)
                        if token == "sk-REDACTED":
                            continue
                        suspicious.append((label, lineno, token))
    except UnicodeDecodeError:
        suspicious.append(("decode_error", 0, f"Failed to decode {path}"))
    return suspicious, unsanitized


def gather_files(path: Path):
    if path.is_file():
        return [path]
    return sorted(
        list(path.glob("*.jsonl"))
        + list(path.glob("*.jsonl.metadata.json"))
        + list(path.glob("*.metadata.json"))
    )


def scan_paths(paths):
    issues = []
    for path in paths:
        files = gather_files(path)
        for file in files:
            suspicious, unsanitized = scan_file(file)
            for label, lineno, token in suspicious:
                snippet = token[:80] + ("..." if len(token) > 80 else "")
                issues.append(f"{file}:{lineno} [{label}] -> {snippet}")
            for lineno, line in unsanitized:
                snippet = line[:80] + ("..." if len(line) > 80 else "")
                issues.append(f"{file}:{lineno} [api_key] -> {snippet}")
    return issues


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scan JSONL logs for accidental secrets or unsanitized api_key fields."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("logs_clean")],
        help="Paths to scan (files or directories). Defaults to logs_clean.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    issues = scan_paths(args.paths)
    if issues:
        print("Potential secret references detected:")
        for issue in issues:
            print(" -", issue)
        print(f"\nTOTAL: {len(issues)} issue(s) detected.")
        return 1
    print("No suspicious tokens or unsanitized api_key references found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
