# OTEL Trace Template

This folder holds the union/“superset” OpenTelemetry schema observed across every benchmark trace we’ve collected so far (image scoring, financial analyzer, Tree-of-Thoughts, etc.). Future exporters should reuse these field names verbatim whenever possible **and preserve the ordering/layering shown in the template** so the shared parsing/analysis tooling can ingest traces without custom adapters. If a field doesn’t apply, omit it or set it to `0` per the guidelines below.

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
| `gen_ai.operation.name` | **Standard OTEL field** - Predefined values: `chat`, `create_agent`, `embeddings`, `execute_tool`, `generate_content`, `invoke_agent`, `text_completion`. Use when available, but analysis tools support fallback detection via span name patterns. |
| `gen_ai.system` / `gen_ai.request.model` | Provider + model identifier. |
| `gen_ai.response.finish_reasons` | Array of finish reasons (empty array if unknown). |
| `gen_ai.tool.*`, `gcp.vertex.agent.*`, `agent.name`, `tot.*`, `mcp.*`, ... | Optional domain-specific keys mirrored from existing traces; populate when available, otherwise skip.

- CPU/memory utilization is captured via the separate metrics feed (see below), so spans do not need to embed those values unless you have a scenario-specific reason.

Optional but encouraged:

- `communication.*` sizes when payloads are sent
- Provider-specific payloads (`gcp.vertex.agent.llm_request`, `gen_ai.tool.call.id`, etc.)
- Task-specific metadata (`tot.puzzle_index`, `tot.best_score`, `agent.log`, ...)

### Run Outcome Attributes

Use `run.*` to record the overall outcome of a run (execution success/failure) and the correctness judgement. These are higher-level summaries and do not replace `status.*` or `agent.failure.*`.

| Attribute | Notes |
| --- | --- |
| `run.outcome` | Execution outcome enum: `success` or `failure`. |
| `run.outcome_reason` | Optional short reason for the outcome. |
| `run.judgement` | Correctness enum: `correct` or `wrong` or `unknown`. |
| `run.judgement_reason` | Optional short reason for the judgement. |

LangGraph benchmarks emit these judgement attributes during execution (default: HotpotQA-style token F1; optional LLM-as-judge against the gold answer) so correctness is visible directly in the trace without post-processing.

### Agent Outcome Attributes

Use the shared `agent.*` namespace to describe retry metadata, failure reasons, and whether a call ended up being useless. These attributes make it possible to aggregate “why did we retry?” across all benchmarks without bespoke parsers.

| Attribute | Notes |
| --- | --- |
| `agent.retry.attempt_number` | 1-based counter for the current attempt of a logical call. Only set when the caller knows this is a retry. |
| `agent.retry.trigger` | Short enum string describing what demanded the retry. Use the constants exported from `mas_traces.langgraph_otel.AgentRetryTrigger` (e.g., `quality`, `relevance_guard`, `guard_fail`, `timeout`, `system`, `upstream`). |
| `agent.retry.previous_span_id` | 16-char hex span id for the failed attempt that triggered this retry (when available). |
| `agent.retry.reason` | Optional human-readable explanation of the trigger. |
| `agent.failure.category` | High-level category when the call failed. Use values from `mas_traces.langgraph_otel.AgentFailureCategory` (`guard`, `system`, `upstream`, `timeout`, `quality`). |
| `agent.failure.reason` | Free-form string with more context (exception summary, guardrail, etc.). |
| `agent.output.useless` | Boolean flag stating whether the LLM/tool call produced a useless result. |
| `agent.output.useless_reason` | Optional string describing why it was useless (duplicate answer, no progress, hung, etc.). |

Benchmarks should prefer setting these attributes at runtime (before or immediately after the call) via the helpers in `mas_traces.langgraph_otel` so downstream dashboards can reason about retries and useless turns without post-processing. When runtime detection is not possible, emit an OTEL event describing the guard/failure, then run a post-processing script that backfills the same attributes with the values inferred offline.

### Events

Spans can attach `events` with `timestamp` + `attributes`. Use them for step-by-step summaries (`tot.summary`, `agent.log`, etc.) so downstream UIs can reconstruct the execution path.

## Template Files

### File Format Standard

**Standard JSONL Format (Required for Production)**:
- Each line contains **one complete JSON object** (no outer array `[]`)
- File extension: `.jsonl`
- Example:
  ```jsonl
  {"trace_id": "abc", "span_id": "1", ...}
  {"trace_id": "abc", "span_id": "2", ...}
  {"trace_id": "def", "span_id": "3", ...}
  ```

Template files provided:
- `otel_span_template.jsonl` - **Standard JSONL format** (one complete JSON object per line, no outer array). **Use this as the reference for exports.**
- `otel_span_template_for_human_reading.json` - Legacy JSON array format (deprecated, kept for human-reading reference only). Do not use this format for new exports.

**Note**: To view the structure of the JSONL template in a readable format, you can use: `python3 -m json.tool < otel_span_template.jsonl`


If a field truly doesn't apply, omit it rather than renaming—scripts already treat missing keys as "not recorded".

## CPU/Memory Metrics

CPU and memory metrics are collected separately from spans (e.g., via a `PeriodicExportingMetricReader`). Use `otel_metrics_template.jsonl` as the reference when emitting those metrics:

- Emit `process.cpu.usage` (`%`) and `process.memory.usage_bytes` (`bytes`) with the documented structure (`timestamp`, `metric_name`, `unit`, `data_points`, `resource`, `scope`).
- Write them as **standard JSONL format** (one metric record per line, no outer array) alongside your span logs, typically under a `metrics/` directory.
- Use `.jsonl` file extension
- Each line contains one complete metric object: `{"timestamp": "...", "metric_name": "...", ...}`
- Do **not** insert those periodic readings directly into span payloads; span attributes should remain focused on trace context, while the metrics file provides the time-series feed for dashboards.

Template files provided:
- `otel_metrics_template.jsonl` - **Standard JSONL format** (one complete metric object per line, no outer array). **Use this as the reference for exports.**
- `otel_metrics_template_for_human_reading.json` - Legacy JSON array format (deprecated, kept for human-reading reference only). Do not use this format for new exports.

**Note**: To view the structure of the JSONL template in a readable format, you can use: `python3 -m json.tool < otel_metrics_template.jsonl | head -30`
