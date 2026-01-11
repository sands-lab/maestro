#!/usr/bin/env python3
"""
Translate raw JSONL span logs into the normalized otel_span_template.json layout.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "logs"
DEFAULT_DEST = Path(__file__).resolve().parents[1] / "translated_traces"

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
        description="Translate analyzer JSONL spans into the otel_span_template format."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Directory containing financial_analyzer_traces-*.jsonl files (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Directory to write translated JSON files (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing translated files.",
    )
    return parser.parse_args()


def iso_to_ns(value: str | None) -> int:
    if not value:
        return 0
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)


def strip_hex_prefix(value: str | None) -> str | None:
    if not value:
        return None
    value = value.lower()
    return value[2:] if value.startswith("0x") else value


def map_attributes(raw: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key in TEMPLATE_ATTRIBUTE_KEYS:
        if key in raw:
            mapped[key] = raw[key]
    return mapped


def translate_line(entry: dict[str, Any]) -> dict[str, Any]:
    attributes = entry.get("attributes", {})
    events_raw = entry.get("events") or []
    resource = entry.get("resource", {}).get("attributes", {})

    start_ns = iso_to_ns(entry.get("start_time"))
    end_ns = iso_to_ns(entry.get("end_time"))

    translated = {
        "trace_id": strip_hex_prefix(entry.get("context", {}).get("trace_id")),
        "span_id": strip_hex_prefix(entry.get("context", {}).get("span_id")),
        "parent_span_id": strip_hex_prefix(entry.get("parent_id")),
        "name": entry.get("name"),
        "agent_name": attributes.get("gen_ai.agent.name"),
        "start_time": start_ns,
        "end_time": end_ns,
        "duration_ns": max(0, end_ns - start_ns),
        "status": entry.get("status", {}),
        "attributes": map_attributes(attributes),
        "communication": {
            "is_in_process_call": False,
            "input_message_size_bytes": attributes.get(
                "communication.input_message_size_bytes", 0
            ),
            "output_message_size_bytes": attributes.get(
                "communication.output_message_size_bytes", 0
            ),
            "total_message_size_bytes": attributes.get(
                "communication.total_message_size_bytes", 0
            ),
        },
        "events": [
            {
                "name": event.get("name"),
                "timestamp": iso_to_ns(event.get("timestamp")),
                "attributes": event.get("attributes", {}),
            }
            for event in events_raw
        ],
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
    return translated


def translate_file(source_path: Path, dest_dir: Path, overwrite: bool) -> Path | None:
    output_path = dest_dir / f"{source_path.stem}.translated.json"
    if output_path.exists() and not overwrite:
        print(f"Skipping existing {output_path}")
        return None

    translated_spans: list[dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            translated_spans.append(translate_line(entry))

    dest_dir.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(translated_spans, handle, indent=2)
    return output_path


def main():
    args = parse_args()
    source_dir: Path = args.source
    dest_dir: Path = args.dest

    files = sorted(source_dir.glob("financial_analyzer_traces-*.jsonl"))
    if not files:
        print(f"No trace files found in {source_dir}")
        return

    for file_path in files:
        if file_path.name.endswith(".metadata.json"):
            continue
        translated = translate_file(file_path, dest_dir, args.overwrite)
        if translated:
            print(f"Wrote {translated}")


if __name__ == "__main__":
    main()
