#!/usr/bin/env python3
"""
Trace Data Converter
Converts collected OTLP trace data to template format and outputs as JSONL
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def convert_span_kind(kind: int) -> str:
    """Convert numeric span kind to string"""
    kind_mapping = {
        0: "INTERNAL",
        1: "SERVER",
        2: "CLIENT",
        3: "PRODUCER",
        4: "CONSUMER",
    }
    return kind_mapping.get(kind, "INTERNAL")


def extract_agent_name(attributes: Dict[str, Any], name: str) -> str:
    """Extract agent name from attributes or span name"""
    # Try to get agent name from various attribute fields
    agent_name_fields = [
        "gen_ai.agent.name",
        "gen_ai.system",
        "service.name",
        "traceloop.entity.name",
    ]

    for field in agent_name_fields:
        if field in attributes and attributes[field]:
            return str(attributes[field])

    # Fallback to span name or "unknown"
    return name if name else "unknown"


def calculate_duration(start_time: int, end_time: int) -> int:
    """Calculate duration in nanoseconds"""
    return end_time - start_time if end_time > start_time else 0


def map_attributes_to_template(attributes: Dict[str, Any]) -> Dict[str, Any]:
    """Map collected attributes to template format"""
    template_attrs = {}

    # Direct mappings
    direct_mappings = {
        # GenAI attributes
        "gen_ai.operation.name": "gen_ai.operation.name",
        "gen_ai.system": "gen_ai.system",
        "gen_ai.agent.name": "gen_ai.agent.name",
        "gen_ai.agent.description": "gen_ai.agent.description",
        "gen_ai.request.model": "gen_ai.request.model",
        "gen_ai.conversation.id": "gen_ai.conversation.id",
        "gen_ai.tool.name": "gen_ai.tool.name",
        "gen_ai.tool.type": "gen_ai.tool.type",
        "gen_ai.tool.call.id": "gen_ai.tool.call.id",
        "gen_ai.tool.description": "gen_ai.tool.description",
        "gen_ai.usage.input_tokens": "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens": "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens": "gen_ai.usage.total_tokens",
        "gen_ai.llm.call.count": "gen_ai.llm.call.count",
        "gen_ai.mcp.call.count": "gen_ai.mcp.call.count",
        "gen_ai.response.finish_reasons": "gen_ai.response.finish_reasons",
        # MCP attributes
        "mcp.server": "mcp.server",
        "mcp.tool": "mcp.tool",
        # GCP Vertex attributes
        "gcp.vertex.agent.llm_request": "gcp.vertex.agent.llm_request",
        "gcp.vertex.agent.llm_response": "gcp.vertex.agent.llm_response",
        "gcp.vertex.agent.tool_call_args": "gcp.vertex.agent.tool_call_args",
        "gcp.vertex.agent.tool_response": "gcp.vertex.agent.tool_response",
        "gcp.vertex.agent.invocation_id": "gcp.vertex.agent.invocation_id",
        "gcp.vertex.agent.session_id": "gcp.vertex.agent.session_id",
        "gcp.vertex.agent.event_id": "gcp.vertex.agent.event_id",
        # Communication attributes
        "communication.input_message_size_bytes": "communication.input_message_size_bytes",
        "communication.output_message_size_bytes": "communication.output_message_size_bytes",
        "communication.total_message_size_bytes": "communication.total_message_size_bytes",
        # Agent log
        "agent.log": "agent.log",
    }

    # Apply direct mappings
    for source_key, target_key in direct_mappings.items():
        if source_key in attributes:
            template_attrs[target_key] = attributes[source_key]

    # Handle special mappings and conversions

    # Map token usage fields
    if "gen_ai.usage.prompt_tokens" in attributes:
        template_attrs["gen_ai.usage.input_tokens"] = attributes[
            "gen_ai.usage.prompt_tokens"
        ]
    if "gen_ai.usage.completion_tokens" in attributes:
        template_attrs["gen_ai.usage.output_tokens"] = attributes[
            "gen_ai.usage.completion_tokens"
        ]
    if "llm.usage.total_tokens" in attributes:
        template_attrs["gen_ai.usage.total_tokens"] = attributes[
            "llm.usage.total_tokens"
        ]

    # Handle session ID mapping
    if "session.id" in attributes:
        if "gen_ai.conversation.id" not in template_attrs:
            template_attrs["gen_ai.conversation.id"] = attributes["session.id"]

    # Map agent information
    if "gen_ai.agent.id" in attributes:
        template_attrs["gen_ai.agent.name"] = attributes["gen_ai.agent.id"]

    # Convert finish reasons to array if it's a string
    if "gen_ai.completion.0.finish_reason" in attributes:
        finish_reason = attributes["gen_ai.completion.0.finish_reason"]
        template_attrs["gen_ai.response.finish_reasons"] = (
            [finish_reason] if finish_reason else []
        )

    # Extract operation name from span name or attributes
    if "gen_ai.operation.name" not in template_attrs:
        if any(key.startswith("llm.") for key in attributes.keys()):
            template_attrs["gen_ai.operation.name"] = "call_llm"
        elif any(key.startswith("gen_ai.tool.") for key in attributes.keys()):
            template_attrs["gen_ai.operation.name"] = "execute_tool"
        elif any(key.startswith("gen_ai.agent.") for key in attributes.keys()):
            template_attrs["gen_ai.operation.name"] = "invoke_agent"

    return template_attrs


def convert_span_to_template(
    span: Dict[str, Any], resource_attrs: Dict[str, Any]
) -> Dict[str, Any]:
    """Convert a single span to template format"""

    # Extract basic span information
    trace_id = span.get("trace_id", "")
    span_id = span.get("span_id", "")
    parent_span_id = span.get("parent_span_id", "") or None
    name = span.get("name", "")
    kind = convert_span_kind(span.get("kind", 0))
    start_time = span.get("start_time_unix_nano", 0)
    end_time = span.get("end_time_unix_nano", 0)
    duration_ns = calculate_duration(start_time, end_time)

    # Extract attributes
    span_attrs = span.get("attributes", {})
    agent_name = extract_agent_name(span_attrs, name)

    # Map attributes to template format
    template_attrs = map_attributes_to_template(span_attrs)

    # Determine status
    status = {"status_code": "UNSET", "description": ""}

    # Check span status
    span_status = span.get("status", {})
    if span_status:
        status_code = span_status.get("code", 0)
        if status_code == 1:
            status["status_code"] = "OK"
        elif status_code == 2:
            status["status_code"] = "ERROR"

        status["description"] = span_status.get("message", "")

    # Handle communication data
    communication = {
        "is_in_process_call": False,
        "input_message_size_bytes": template_attrs.get(
            "communication.input_message_size_bytes", 0
        ),
        "output_message_size_bytes": template_attrs.get(
            "communication.output_message_size_bytes", 0
        ),
        "total_message_size_bytes": template_attrs.get(
            "communication.total_message_size_bytes", 0
        ),
    }

    # Extract events
    events = []
    for event in span.get("events", []):
        event_attrs = {}
        for key, value in event.get("attributes", {}).items():
            if key in ["tot.summary", "agent.log"]:
                event_attrs[key] = value

        events.append(
            {
                "name": event.get("name", ""),
                "timestamp": event.get("time_unix_nano", 0),
                "attributes": event_attrs,
            }
        )

    # Build resource information
    resource = {
        "attributes": {
            "service.name": resource_attrs.get("service.name", "unknown"),
            "service.version": resource_attrs.get("service.version", ""),
            "deployment.environment": resource_attrs.get("deployment.environment", ""),
            "telemetry.sdk.name": resource_attrs.get("telemetry.sdk.name", ""),
            "telemetry.sdk.language": resource_attrs.get("telemetry.sdk.language", ""),
            "telemetry.sdk.version": resource_attrs.get("telemetry.sdk.version", ""),
            "host.name": resource_attrs.get("host.name", ""),
        }
    }

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "agent_name": agent_name,
        "start_time": start_time,
        "end_time": end_time,
        "duration_ns": duration_ns,
        "kind": kind,
        "status": status,
        "attributes": template_attrs,
        "communication": communication,
        "events": events,
        "resource": resource,
    }


def process_trace_file(input_file: Path) -> List[Dict[str, Any]]:
    """Process a single trace file and extract all spans"""
    converted_spans = []

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Handle the collector's storage format
        trace_entries = data.get("data", [])
        if not isinstance(trace_entries, list):
            trace_entries = [trace_entries]

        for entry in trace_entries:
            # Skip entries that weren't parsed successfully
            if not entry.get("parsed_successfully", False):
                continue

            entry_data = entry.get("data", {})
            resource_spans = entry_data.get("resource_spans", [])

            for resource_span in resource_spans:
                # Extract resource attributes
                resource_attrs = resource_span.get("resource", {}).get("attributes", {})

                # Process all scope spans
                for scope_span in resource_span.get("scope_spans", []):
                    spans = scope_span.get("spans", [])

                    for span in spans:
                        try:
                            converted_span = convert_span_to_template(
                                span, resource_attrs
                            )
                            converted_spans.append(converted_span)
                        except Exception as e:
                            print(
                                f"Warning: Failed to convert span {span.get('span_id', 'unknown')}: {e}"
                            )
                            continue

    except Exception as e:
        print(f"Error processing file {input_file}: {e}")

    return converted_spans


def main():
    parser = argparse.ArgumentParser(
        description="Convert collected trace data to template format"
    )
    parser.add_argument(
        "input", help="Input trace file or directory containing trace files"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="converted_traces.jsonl",
        help="Output JSONL file (default: converted_traces.jsonl)",
    )
    parser.add_argument(
        "--template", help="Template file to validate against (optional)"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    # Collect all trace files to process
    trace_files = []

    if input_path.is_file():
        if input_path.suffix == ".json":
            trace_files.append(input_path)
        else:
            print(f"Error: Input file {input_path} is not a JSON file")
            return 1
    elif input_path.is_dir():
        # Find all JSON files in directory
        trace_files.extend(input_path.glob("*.json"))
        trace_files.extend(input_path.glob("**/*.json"))
    else:
        print(f"Error: Input path {input_path} does not exist")
        return 1

    if not trace_files:
        print("No JSON trace files found to process")
        return 1

    print(f"Processing {len(trace_files)} trace files...")

    all_spans = []

    # Process each trace file
    for trace_file in trace_files:
        print(f"Processing: {trace_file}")
        spans = process_trace_file(trace_file)
        all_spans.extend(spans)
        print(f"  Extracted {len(spans)} spans")

    print(f"Total spans extracted: {len(all_spans)}")

    # Write to JSONL format
    with open(output_path, "w", encoding="utf-8") as f:
        for span in all_spans:
            json.dump(span, f, ensure_ascii=False)
            f.write("\n")

    print(f"Converted traces written to: {output_path}")

    # Show sample output
    if all_spans:
        print("\nSample converted span:")
        print(json.dumps(all_spans[0], indent=2)[:500] + "...")

    return 0


if __name__ == "__main__":
    exit(main())
