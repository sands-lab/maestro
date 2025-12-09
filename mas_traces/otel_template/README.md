# OTEL Trace Template

This folder holds a minimal OpenTelemetry JSON schema that every benchmark/exporter in `mas_traces` should satisfy. The goal is to keep span files interoperable while giving each scenario room to add domain-specific attributes. **Parsing and analysis scripts assume the exact field names described below—please reuse them verbatim whenever possible.**

## Required Span Fields

| Field | Notes |
| --- | --- |
| `trace_id` | 32-char hex string shared across spans in a trace. |
| `span_id` | 16-char hex string unique per span. |
| `parent_span_id` | `null` for roots; child spans must set this. |
| `name` | Human-readable operation (`call_llm`, `execute_tool`, etc.). |
| `kind` | OTEL canonical kind (`INTERNAL`, `SERVER`, `CLIENT`, ...). |
| `start_time`, `end_time` | Unix-nano timestamps. |
| `status.status_code` | `UNSET`, `OK`, or `ERROR`. Use `status.description` for context. |
| `resource.attributes.service.name` | Identifies the benchmark/app. |
| `resource.attributes.telemetry.*` | SDK metadata (`opentelemetry`, language, version). |

Every span MUST include the LLM metrics below.

### LLM / MCP Metrics

| Attribute | Requirement |
| --- | --- |
| `gen_ai.usage.input_tokens` | Prompt tokens consumed for the operation. Set to `0` if unavailable. |
| `gen_ai.usage.output_tokens` | Completion tokens returned. `0` if not applicable. |
| `gen_ai.usage.total_tokens` | Sum of input + output (or provider-reported total). |
| `gen_ai.llm.call.count` | Number of LLM calls represented by the span. Default `1`; use `0` if no LLM call happened. |
| `gen_ai.mcp.call.count` | Number of MCP tool/server invocations. Use `0` if unused. |

Optional but encouraged:

- `gen_ai.request.model`
- `gen_ai.system` or provider name
- `gen_ai.operation.name` (`call_llm`, `execute_tool`, `invoke_agent`, ...)
- `gen_ai.response.finish_reasons`
- `mcp.server`, `mcp.tool`, or other tool metadata
- Token/cost breakdowns (`prompt_tokens`, `completion_tokens`, latencies, etc.)

### Events

Spans can attach `events` with `timestamp` + `attributes`. Use them for step-by-step summaries (`tot.summary`, `agent.log`, etc.) so downstream UIs can reconstruct the execution path.

## Template File

`otel_span_template.json` shows the canonical structure with placeholder values. Copy it when wiring new exporters to ensure the base schema + metric attributes are populated before writing scenario-specific data.
