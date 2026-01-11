#!/usr/bin/env python3
"""Backfill metadata sidecar files for FAQ Redis semantic cache trace logs."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from typing import Any

APP_NAME = "faq_redis_semantic_cache_naive"
METADATA_VERSION = 1
LLM_BACKEND = "openai"


def _parse_cli_argv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return shlex.split(raw)


def _parse_env_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid env override '{pair}'. Use KEY=VALUE.")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env override '{pair}'. Missing key.")
        overrides[key] = value
    return overrides


def _derive_run_id(log_path: str) -> str:
    stem = os.path.splitext(os.path.basename(log_path))[0]
    if stem.startswith("run_"):
        return stem.split("run_", 1)[1]
    return stem


def _build_metadata(
    log_path: str,
    run_id: str,
    args: argparse.Namespace,
    cli_argv: list[str],
    env_overrides: dict[str, str],
    bench_root: str,
) -> dict[str, Any]:
    return {
        "metadata_version": METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_name": APP_NAME,
        "python_version": sys.version,
        "cli_argv": cli_argv,
        "redis_url": args.redis_url,
        "distance_threshold": args.distance_threshold,
        "llm_backend": LLM_BACKEND,
        "llm_model": args.llm_model,
        "ttl_seconds": args.ttl_seconds,
        "skip_redis": args.skip_redis,
        "llm_rate_limit": args.llm_rate_limit,
        "env_overrides": env_overrides,
        "trace_log": os.path.relpath(log_path, start=bench_root),
        "trace_log_basename": os.path.basename(log_path),
        "status": args.status,
        "notes": "Backfilled metadata (assumed defaults).",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create metadata sidecars for trace logs.")
    parser.add_argument("--logs-dir", default="logs", help="Directory containing trace logs.")
    parser.add_argument(
        "--pattern",
        default="run_*.log",
        help="Glob pattern (relative to logs dir) used to locate trace files.",
    )
    parser.add_argument("--redis-url", default="redis://localhost:6379", help="Redis URL stored in metadata.")
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=0.3,
        help="Cosine distance threshold stored in metadata.",
    )
    parser.add_argument("--llm-model", default="gpt-4o-mini", help="LLM model recorded for the run.")
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=86400,
        help="TTL (in seconds) recorded for the run.",
    )
    parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="Mark runs as having skipped the Redis-backed phase.",
    )
    parser.add_argument(
        "--llm-rate-limit",
        type=float,
        default=None,
        help="LLM requests-per-minute limit recorded in metadata.",
    )
    parser.add_argument(
        "--cli-argv",
        default="",
        help="Quoted string representing the CLI invocation to store in metadata.",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment override entries to embed in metadata (can repeat).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite metadata files when they already exist.",
    )
    parser.add_argument(
        "--status",
        default="ok",
        help="Status string to store in metadata (e.g., ok, failed, partial).",
    )
    args = parser.parse_args()

    logs_dir = os.path.abspath(args.logs_dir)
    bench_root = os.path.dirname(logs_dir)
    pattern = os.path.join(logs_dir, args.pattern)
    trace_paths = sorted(glob.glob(pattern))
    if not trace_paths:
        raise SystemExit(f"No trace logs found via pattern {pattern}")

    cli_argv = _parse_cli_argv(args.cli_argv)
    try:
        env_overrides = _parse_env_overrides(args.env)
    except ValueError as exc:  # pragma: no cover - CLI argument validation
        raise SystemExit(str(exc)) from exc

    for trace_path in trace_paths:
        run_id = _derive_run_id(trace_path)
        metadata = _build_metadata(trace_path, run_id, args, cli_argv, env_overrides, bench_root)
        metadata_path = f"{trace_path}.metadata.json"

        if os.path.exists(metadata_path) and not args.overwrite:
            print(f"Skipping existing metadata: {metadata_path}")
            continue

        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
        print(f"Wrote {metadata_path}")


if __name__ == "__main__":
    main()
