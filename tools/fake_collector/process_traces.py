import json
import os
import uuid
from collections import defaultdict
from datetime import datetime


def has_actual_tool_calls(span_attrs):
    """Check if span has actual tool calls by parsing traceloop.entity.output JSON"""
    span_output = span_attrs.get("traceloop.entity.output", "")

    if not span_output:
        return False, []

    try:
        # Try to parse the JSON string
        output_data = json.loads(span_output)
        if not isinstance(output_data, dict) and isinstance(output_data, str):
            output_data = json.loads(output_data)

        # Look for tool_calls in the output
        tool_calls = output_data.get("tool_calls", [])

        if tool_calls and len(tool_calls) > 0:
            # Extract all tool calls
            tool_info_list = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("tool_name")
                tool_type = "Builtin"  # Default type
                if tool_name:
                    tool_info_list.append(
                        {"tool_name": tool_name, "tool_type": tool_type}
                    )
            return True, tool_info_list

    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    return False, []


def determine_operation_name(span_attrs, span_name):
    """Determine gen_ai.operation.name based on span attributes and name"""
    # First check if it has actual tool calls
    has_tools, _ = has_actual_tool_calls(span_attrs)
    if has_tools:
        return "execute_tool"

    # Check if it's an LLM call
    if any(
        key in span_attrs for key in ["gen_ai.request.model", "llm.request.type"]
    ) and any(keyword in span_name.lower() for keyword in ["chat", "openai", "llm"]):
        # Check if token data exists
        input_tokens = span_attrs.get("gen_ai.usage.input_tokens") or span_attrs.get(
            "gen_ai.usage.prompt_tokens"
        )
        output_tokens = span_attrs.get("gen_ai.usage.output_tokens") or span_attrs.get(
            "gen_ai.usage.completion_tokens"
        )
        total_tokens = span_attrs.get("gen_ai.usage.total_tokens") or span_attrs.get(
            "llm.usage.total_tokens"
        )

        # Only return call_llm if token data exists
        if input_tokens or output_tokens or total_tokens:
            return "call_llm"
        else:
            return None

    # Check if it's agent invocation/creation
    if "create_agent" in span_name.lower() or (
        "agent" in span_name.lower()
        and any(
            keyword in span_name.lower()
            for keyword in ["create", "invoke", "initialize"]
        )
    ):
        return "invoke_agent"

    # Default classification based on span name patterns
    if any(
        keyword in span_name.lower()
        for keyword in ["chat", "completion", "openai", "llm"]
    ):
        return "call_llm"
    else:
        return "invoke_agent"


def extract_tool_info(span_attrs, span_name, operation_name):
    """Extract tool name and type from attributes only if it's actually a tool execution"""
    if operation_name != "execute_tool":
        return []

    # Use the has_actual_tool_calls function to get tool info
    has_tools, tool_info_list = has_actual_tool_calls(span_attrs)

    if has_tools:
        return tool_info_list

    return []


def normalize_token_fields(span_attrs):
    """Normalize token-related fields to standard names"""
    input_tokens = (
        span_attrs.get("gen_ai.usage.input_tokens")
        or span_attrs.get("gen_ai.usage.prompt_tokens")
        or 0
    )

    output_tokens = (
        span_attrs.get("gen_ai.usage.output_tokens")
        or span_attrs.get("gen_ai.usage.completion_tokens")
        or 0
    )

    total_tokens = (
        span_attrs.get("gen_ai.usage.total_tokens")
        or span_attrs.get("llm.usage.total_tokens")
        or (input_tokens + output_tokens if input_tokens or output_tokens else 0)
    )

    return input_tokens, output_tokens, total_tokens


def load_json_files(storage_path):
    """Load all JSON files from storage directory"""
    files_data = []
    for filename in os.listdir(storage_path):
        if filename.endswith(".json"):
            with open(os.path.join(storage_path, filename), "r") as f:
                data = json.load(f)
                files_data.append(data)
    return files_data


def extract_spans_by_agent(files_data):
    """Extract spans grouped by agent name"""
    agent_spans = defaultdict(list)
    conversion_uuid = str(uuid.uuid4())

    for file_data in files_data:
        for entry in file_data.get("data", []):
            entry_data = entry.get("data", {})
            resource_spans = entry_data.get("resource_spans", [])

            for resource_span in resource_spans:
                # Get agent name from service.name
                agent_name = (
                    resource_span.get("resource", {})
                    .get("attributes", {})
                    .get("service.name")
                )
                if not agent_name:
                    continue

                # Extract all spans from scope_spans
                scope_spans = resource_span.get("scope_spans", [])
                for scope_span in scope_spans:
                    spans = scope_span.get("spans", [])
                    for span in spans:
                        span_attrs = span.get("attributes", {})
                        span_name = span.get("name", "")

                        # Determine operation name
                        operation_name = determine_operation_name(span_attrs, span_name)

                        # Skip call_llm spans without token data
                        if operation_name == "call_llm":
                            input_tokens, output_tokens, total_tokens = (
                                normalize_token_fields(span_attrs)
                            )
                            if not (input_tokens or output_tokens or total_tokens):
                                continue

                        # Extract tool information only for actual tool executions
                        tool_info_list = extract_tool_info(
                            span_attrs, span_name, operation_name
                        )

                        # Normalize token fields
                        input_tokens, output_tokens, total_tokens = (
                            normalize_token_fields(span_attrs)
                        )

                        # Create modified resource with UUID
                        modified_resource = resource_span.get("resource", {}).copy()
                        if "attributes" in modified_resource:
                            modified_resource["attributes"] = modified_resource[
                                "attributes"
                            ].copy()
                            modified_resource["attributes"]["service.name"] = (
                                conversion_uuid
                            )

                        # If there are multiple tools, create multiple span entries
                        if tool_info_list and operation_name == "execute_tool":
                            for tool_info in tool_info_list:
                                # Build required attributes for each tool
                                required_attributes = {
                                    "gen_ai.operation.name": operation_name,
                                    "gen_ai.agent.name": agent_name,
                                    "gen_ai.tool.name": tool_info["tool_name"],
                                    "gen_ai.tool.type": tool_info["tool_type"],
                                }

                                # Add token fields if present
                                if input_tokens or output_tokens or total_tokens:
                                    required_attributes.update(
                                        {
                                            "gen_ai.usage.input_tokens": input_tokens,
                                            "gen_ai.usage.output_tokens": output_tokens,
                                            "gen_ai.usage.total_tokens": total_tokens,
                                        }
                                    )

                                # Add other optional fields from template
                                optional_fields = [
                                    "gen_ai.system",
                                    "gen_ai.agent.description",
                                    "gen_ai.request.model",
                                    "gen_ai.conversation.id",
                                    "gen_ai.tool.call.id",
                                    "gen_ai.tool.description",
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

                                for field in optional_fields:
                                    if field in span_attrs:
                                        required_attributes[field] = span_attrs[field]

                                span_data = {
                                    "trace_id": span.get("trace_id"),
                                    "span_id": span.get("span_id"),
                                    "parent_span_id": span.get("parent_span_id"),
                                    "name": span_name,
                                    "agent_name": agent_name,
                                    "start_time": span.get("start_time_unix_nano", 0),
                                    "end_time": span.get("end_time_unix_nano", 0),
                                    "duration_ns": span.get("end_time_unix_nano", 0)
                                    - span.get("start_time_unix_nano", 0),
                                    "kind": span.get("kind", "INTERNAL"),
                                    "status": span.get(
                                        "status",
                                        {"status_code": "UNSET", "description": ""},
                                    ),
                                    "attributes": required_attributes,
                                    "communication": {
                                        "is_in_process_call": False,
                                        "input_message_size_bytes": span_attrs.get(
                                            "communication.input_message_size_bytes", 0
                                        ),
                                        "output_message_size_bytes": span_attrs.get(
                                            "communication.output_message_size_bytes", 0
                                        ),
                                        "total_message_size_bytes": span_attrs.get(
                                            "communication.total_message_size_bytes", 0
                                        ),
                                    },
                                    "events": span.get("events", []),
                                    "resource": modified_resource,
                                }
                                agent_spans[agent_name].append(span_data)
                        else:
                            # Handle non-tool operations or operations without tools
                            required_attributes = {
                                "gen_ai.operation.name": operation_name,
                                "gen_ai.agent.name": agent_name,
                            }

                            # Add token fields if present
                            if input_tokens or output_tokens or total_tokens:
                                required_attributes.update(
                                    {
                                        "gen_ai.usage.input_tokens": input_tokens,
                                        "gen_ai.usage.output_tokens": output_tokens,
                                        "gen_ai.usage.total_tokens": total_tokens,
                                    }
                                )

                            # Add other optional fields
                            optional_fields = [
                                "gen_ai.system",
                                "gen_ai.agent.description",
                                "gen_ai.request.model",
                                "gen_ai.conversation.id",
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

                            for field in optional_fields:
                                if field in span_attrs:
                                    required_attributes[field] = span_attrs[field]

                            span_data = {
                                "trace_id": span.get("trace_id"),
                                "span_id": span.get("span_id"),
                                "parent_span_id": span.get("parent_span_id"),
                                "name": span_name,
                                "agent_name": agent_name,
                                "start_time": span.get("start_time_unix_nano", 0),
                                "end_time": span.get("end_time_unix_nano", 0),
                                "duration_ns": span.get("end_time_unix_nano", 0)
                                - span.get("start_time_unix_nano", 0),
                                "kind": span.get("kind", "INTERNAL"),
                                "status": span.get(
                                    "status",
                                    {"status_code": "UNSET", "description": ""},
                                ),
                                "attributes": required_attributes,
                                "communication": {
                                    "is_in_process_call": False,
                                    "input_message_size_bytes": span_attrs.get(
                                        "communication.input_message_size_bytes", 0
                                    ),
                                    "output_message_size_bytes": span_attrs.get(
                                        "communication.output_message_size_bytes", 0
                                    ),
                                    "total_message_size_bytes": span_attrs.get(
                                        "communication.total_message_size_bytes", 0
                                    ),
                                },
                                "events": span.get("events", []),
                                "resource": modified_resource,
                            }
                            agent_spans[agent_name].append(span_data)

    return agent_spans


def main():
    storage_path = "storage"

    # Create output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join("convert", timestamp)
    os.makedirs(output_dir, exist_ok=True)

    # Load all JSON files
    files_data = load_json_files(storage_path)

    # Extract spans by agent
    agent_spans = extract_spans_by_agent(files_data)

    # Save results for each agent
    for agent_name, spans in agent_spans.items():
        output_filename = os.path.join(output_dir, f"{agent_name}_traces.jsonl")
        with open(output_filename, "w") as f:
            for span in spans:
                f.write(json.dumps(span) + "\n")
        print(f"Saved {len(spans)} spans for {agent_name} to {output_filename}")


if __name__ == "__main__":
    main()
