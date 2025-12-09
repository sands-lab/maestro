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

If a field truly doesn't apply, omit it rather than renaming—scripts already treat missing keys as "not recorded".

**Note**: The `.json` template file is provided in a human-readable format for documentation purposes. **Actual exports should use JSONL format** (one JSON object per line) for efficient streaming and processing. See `otel_span_template.json` for the structure reference, but export as `.jsonl` in your implementation.

## CPU/Memory Metrics

### CPU/Memory Metrics Collection

CPU and memory metrics are collected separately from spans using OpenTelemetry Metrics API with periodic sampling:

- **Collection Mechanism**: Uses `PeriodicExportingMetricReader` with `observable_gauge` metrics
- **Sampling Frequency**: Every 1 second (configurable via `export_interval_millis`)
- **Collection Method**: 
  - CPU: `psutil.Process().cpu_percent(interval=0.1)` - Process CPU usage percentage
  - Memory: `psutil.Process().memory_info().rss` - Process memory RSS in bytes
- **Export Format**: JSONL file (one metric record per line)
- **File Location**: `metrics/{service_name}_{timestamp}.jsonl`

The metrics are collected via callback functions (`_get_process_cpu_usage`, `_get_process_memory_usage`) that are automatically invoked by the OpenTelemetry SDK's background thread every second. This provides uniform time-series data for resource usage visualization.

### Metrics File Structure

Each line in the metrics JSONL file is a metric record with the following structure:

| Field | Description |
| --- | --- |
| `timestamp` | ISO 8601 timestamp when the metric was exported |
| `metric_name` | Metric identifier (`process.cpu.usage`, `process.memory.usage_bytes`, etc.) |
| `description` | Human-readable description |
| `unit` | Unit of measurement (`%`, `bytes`, etc.) |
| `data_points` | Array of data points, each containing `value`, `timestamp` (Unix nanoseconds), and `attributes` |
| `resource.attributes` | Resource attributes (service.name, service.version, deployment.environment, telemetry.*) |
| `scope` | Instrumentation scope name |

### Required Metrics

| Metric Name | Unit | Description |
| --- | --- | --- |
| `process.cpu.usage` | `%` | Process CPU usage percentage (0-100) |
| `process.memory.usage_bytes` | `bytes` | Process memory usage (RSS - Resident Set Size) |

### Span Attributes vs Metrics

- **Span Attributes**: CPU/memory values are recorded inline with each span (event-driven, non-uniform sampling)
- **Metrics File**: CPU/memory values are collected periodically (uniform time-series, 1 second intervals)

For visualization and analysis, use the metrics file for accurate time-series data. Span attributes provide instantaneous values for standard compliance but are not suitable for time-series analysis.

### Metrics Template Files

- **`otel_metrics_template.json`**: Human-readable JSON format for documentation and reference

**Important**: The `.json` file is provided only for human readability. **Actual metrics exports must use JSONL format** (`.jsonl`) for efficient streaming, incremental writes, and compatibility with analysis tools. Each line in the JSONL file should be a complete, valid JSON object representing one metric record.
