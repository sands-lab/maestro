# OTEL Trace Template

This folder holds the union/“superset” OpenTelemetry schema observed across every benchmark trace we’ve collected so far (image scoring, financial analyzer, Tree-of-Thoughts, etc.). Future exporters should reuse these field names verbatim whenever possible so the shared parsing/analysis tooling can ingest traces without custom adapters. If a field doesn’t apply, omit it or set it to `0` per the guidelines below.

## Required Span Fields

| Field | Notes |
| --- | --- |
| `trace_id` | 32-char hex string shared across spans in a trace. |
| `span_id` | 16-char hex string unique per span. |
| `parent_span_id` | `null`/missing for roots; children must set this. |
| `name` | Human-readable operation (`call_llm`, `execute_tool`, `tot.run`, ...). |
| `kind` | OTEL canonical kind (`INTERNAL`, `SERVER`, `CLIENT`, `PRODUCER`, `CONSUMER`). |
| `start_time`, `end_time`, `duration_ns` | Unix-ns timestamps and derived duration. |
| `status.status_code` | `UNSET`, `OK`, or `ERROR`. Use `status.description` for context, if any. |
| `resource.attributes.service.name` | Identifies the benchmark/app (“image-scoring”, “financial-analyzer”, “tree-of-thoughts-benchmark”, ...). |
| `resource.attributes.service.version` | Semantic version for the benchmark build. |
| `resource.attributes.deployment.environment` | `local`, `dev`, `staging`, `prod`, etc. |
| `resource.attributes.telemetry.*` | SDK metadata (`opentelemetry`, `python`, version). |
| `resource.attributes.host.name` | Optional hostname when available. |

Every span MUST include the LLM metrics below, plus optional CPU/memory attributes as available.

### LLM / MCP Metrics

| Attribute | Requirement |
| --- | --- |
| `gen_ai.usage.input_tokens` | Prompt tokens consumed (default `0`). |
| `gen_ai.usage.output_tokens` | Completion tokens returned (default `0`). |
| `gen_ai.usage.total_tokens` | Sum of input + output or provider-reported total (default `0`). |
| `gen_ai.llm.call.count` | Number of LLM requests represented by the span (`0` if none). |
| `gen_ai.mcp.call.count` | Number of MCP server/tool invocations (`0` if none). |
| `gen_ai.operation.name` | `call_llm`, `invoke_agent`, `execute_tool`, etc. |
| `gen_ai.system` / `gen_ai.request.model` | Provider + model identifier. |
| `gen_ai.response.finish_reasons` | Array of finish reasons (empty array if unknown). |
| `gen_ai.tool.*`, `gcp.vertex.agent.*`, `agent.name`, `tot.*`, `mcp.*`, ... | Optional domain-specific keys mirrored from existing traces; populate when available, otherwise skip.

- Record CPU/memory utilization inline by setting `system.cpu.percent`, `process.cpu.percent`, `system.memory.usage_bytes`, and `process.memory.rss_bytes` on the span attributes (default to `0` when unknown).

Optional but encouraged:

- `communication.*` sizes when payloads are sent
- `cpu.`/`memory.` deltas at the span level
- Provider-specific payloads (`gcp.vertex.agent.llm_request`, `gen_ai.tool.call.id`, etc.)
- Task-specific metadata (`tot.puzzle_index`, `tot.best_score`, `agent.log`, ...)

### Events

Spans can attach `events` with `timestamp` + `attributes`. Use them for step-by-step summaries (`tot.summary`, `agent.log`, etc.) so downstream UIs can reconstruct the execution path.

## Template File

`otel_span_template.json` shows the superset structure with placeholder values. Duplicate it (or load/extend programmatically) when wiring new exporters to ensure:

1. Resource attributes are set once per trace/write.
2. Span payloads always contain the common metrics + CPU/memory fields.
3. Optional blocks (`communication`, `events`) follow a consistent shape even when empty.

If a field truly doesn’t apply, omit it rather than renaming—scripts already treat missing keys as “not recorded”.

## Metrics

CPU/memory sampling now happens inline with each span, so the JSON template no longer carries a separate metrics block. If your instrumented app exports additional OTEL metrics streams, keep those in their own files/streams—the span template only defines the per-event payload structure.
