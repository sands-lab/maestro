#!/usr/bin/env python3
"""
Normalize Tree-of-Thoughts OTEL JSONL spans into the shared template format.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "logs"
DEFAULT_DEST = ROOT / "translated_traces"

TEMPLATE_ATTRIBUTE_KEYS = [
    "gen_ai.operation.name",
    "gen_ai.system",
    "gen_ai.agent.name",
    "gen_ai.agent.description",
    "gen_ai.request.model",
    "gen_ai.conversation.id",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.tool.call.id",
    "gen_ai.tool.description",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "gen_ai.llm.call.count",
    "gen_ai.mcp.call.count",
    "gen_ai.response.finish_reasons",
    "mcp.server",
    "mcp.tool",
    "gcp.vertex.agent.llm_request",
    "gcp.vertex.agent.llm_response",
    "gcp.vertex.agent.tool_call_args",
    "gcp.vertex.agent.tool_response",
    "gcp.vertex.agent.invocation_id",
    "gcp.vertex.agent.session_id",
    "gcp.vertex.agent.event_id",
    "agent.log",
    "communication.input_message_size_bytes",
    "communication.output_message_size_bytes",
    "communication.total_message_size_bytes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate Tree-of-Thoughts OTEL spans into template format."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Directory containing run_*.otel.jsonl files (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Directory to write translated JSON (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite translated files if they already exist.",
    )
    return parser.parse_args()


def ensure_ns(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            pass
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    return 0


def map_attributes(raw: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key in TEMPLATE_ATTRIBUTE_KEYS:
        if key in raw:
            mapped[key] = raw[key]
    for key, value in raw.items():
        if key.startswith("tot."):
            mapped[key] = value
    return mapped


def translate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    attributes = entry.get("attributes", {})
    resource = entry.get("resource", {}).get("attributes", {})
    communication = entry.get("communication", {}) or {}

    start_ns = ensure_ns(entry.get("start_time"))
    end_ns = ensure_ns(entry.get("end_time"))

    return {
        "trace_id": entry.get("trace_id"),
        "span_id": entry.get("span_id"),
        "parent_span_id": entry.get("parent_span_id"),
        "name": entry.get("name"),
        "agent_name": entry.get("agent_name") or attributes.get("gen_ai.agent.name"),
        "start_time": start_ns,
        "end_time": end_ns,
        "duration_ns": max(0, ensure_ns(entry.get("duration_ns")) or (end_ns - start_ns)),
        "status": entry.get("status", {}),
        "attributes": map_attributes(attributes),
        "communication": {
            "is_in_process_call": bool(communication.get("is_in_process_call", False)),
            "input_message_size_bytes": communication.get(
                "input_message_size_bytes",
                attributes.get("communication.input_message_size_bytes", 0),
            ),
            "output_message_size_bytes": communication.get(
                "output_message_size_bytes",
                attributes.get("communication.output_message_size_bytes", 0),
            ),
            "total_message_size_bytes": communication.get(
                "total_message_size_bytes",
                attributes.get("communication.total_message_size_bytes", 0),
            ),
        },
        "events": entry.get("events", []),
        "resource": {
            "attributes": {
                "service.name": resource.get("service.name"),
                "service.version": resource.get("service.version"),
                "deployment.environment": resource.get("deployment.environment"),
                "telemetry.sdk.name": resource.get("telemetry.sdk.name"),
                "telemetry.sdk.language": resource.get("telemetry.sdk.language"),
                "telemetry.sdk.version": resource.get("telemetry.sdk.version"),
                "host.name": resource.get("host.name"),
            }
        },
    }


def translate_file(source_path: Path, dest_dir: Path, overwrite: bool) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    output_path = dest_dir / f"{source_path.stem}.translated.json"
    if output_path.exists() and not overwrite:
        print(f"Skipping existing {output_path}")
        return None

    translated: list[dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            translated.append(translate_entry(entry))

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(translated, handle, indent=2)
    return output_path


def main():
    args = parse_args()
    files = sorted(args.source.glob("run_*.otel.jsonl"))
    if not files:
        print(f"No run_*.otel.jsonl files found in {args.source}")
        return

    for file_path in files:
        translated = translate_file(file_path, args.dest, args.overwrite)
        if translated:
            print(f"Wrote {translated}")


if __name__ == "__main__":
    main()
