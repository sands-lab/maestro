#!/usr/bin/env python3
"""
Create metadata sidecar files for existing financial analyzer trace logs.

Usage:
    python scripts/backfill_trace_metadata.py --logs-dir logs

By default the script assumes:
  - Every log except the newest used the Google/Gemini backend
  - The newest log used OpenAI GPT-4o
  - Search provider chain was just the Google Playwright server
Override these defaults with CLI flags as needed.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any

METADATA_VERSION = 1


def _build_metadata(
    log_path: str,
    llm_backend: str,
    llm_model: str,
    search_providers: list[str],
    search_description: str,
    run_id: str,
    workflow_status: str,
    workflow_completed: bool,
) -> dict[str, Any]:
    return {
        "metadata_version": METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "trace_log": os.path.relpath(log_path),
        "trace_log_basename": os.path.basename(log_path),
        "llm_backend": llm_backend,
        "llm_model": llm_model,
        "search_providers_active": search_providers,
        "search_provider_description": search_description,
        "sanity_mode": True,
        "workflow_status": workflow_status,
        "workflow_completed": workflow_completed,
        "cli_argv": [],
        "env_overrides": {},
        "notes": "Backfilled metadata (assumed defaults).",
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill trace metadata sidecars.")
    parser.add_argument("--logs-dir", default="logs", help="Directory containing JSONL trace files.")
    parser.add_argument(
        "--pattern",
        default="financial_analyzer_traces-*.jsonl",
        help="Glob pattern for trace filenames (relative to logs dir).",
    )
    parser.add_argument(
        "--older-llm-backend",
        default="google",
        help="LLM backend for all but the newest trace file.",
    )
    parser.add_argument(
        "--older-llm-model",
        default="gemini-2.5-flash-lite",
        help="Model name for older traces.",
    )
    parser.add_argument(
        "--latest-llm-backend",
        default="openai",
        help="LLM backend for the newest trace file.",
    )
    parser.add_argument(
        "--latest-llm-model",
        default="gpt-4o",
        help="Model name for the newest trace file.",
    )
    parser.add_argument(
        "--search-providers",
        default="g-search",
        help="Comma-separated list of search MCP servers used in these runs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing metadata files if they already exist.",
    )
    parser.add_argument(
        "--status",
        default="unknown",
        help="Workflow status string stored in metadata (e.g., ok, failed, unknown).",
    )
    parser.add_argument(
        "--workflow-completed",
        dest="workflow_completed",
        action="store_true",
        help="Mark backfilled traces as completed (default).",
    )
    parser.add_argument(
        "--workflow-incomplete",
        dest="workflow_completed",
        action="store_false",
        help="Mark backfilled traces as missing a completed workflow.",
    )
    parser.set_defaults(workflow_completed=True)
    args = parser.parse_args()

    logs_dir = os.path.abspath(args.logs_dir)
    pattern = os.path.join(logs_dir, args.pattern)
    trace_paths = sorted(glob.glob(pattern))
    if not trace_paths:
        raise SystemExit(f"No trace files found matching {pattern}")

    search_chain = [token.strip() for token in args.search_providers.split(",") if token.strip()]
    search_description = ", ".join(search_chain)

    newest_index = len(trace_paths) - 1
    for index, trace_path in enumerate(trace_paths):
        run_id = os.path.splitext(os.path.basename(trace_path))[0].replace("financial_analyzer_traces-", "")
        if index == newest_index:
            backend = args.latest_llm_backend
            model = args.latest_llm_model
        else:
            backend = args.older_llm_backend
            model = args.older_llm_model

        metadata = _build_metadata(
            trace_path,
            llm_backend=backend,
            llm_model=model,
            search_providers=search_chain,
            search_description=search_description,
            run_id=run_id,
            workflow_status=args.status,
            workflow_completed=args.workflow_completed,
        )

        meta_path = f"{trace_path}.metadata.json"
        if os.path.exists(meta_path) and not args.overwrite:
            print(f"Skipping existing metadata: {meta_path}")
            continue

        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2, sort_keys=True)
        print(f"Wrote {meta_path}")


if __name__ == "__main__":
    main()
