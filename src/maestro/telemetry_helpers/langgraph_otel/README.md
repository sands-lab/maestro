# LangGraph OTEL Helpers

This package centralizes the telemetry logic shared across every MAS benchmark. Importing the functions from `plugin_monitoring.langgraph_otel` ensures each LangGraph example records spans and metrics that match the repo's OTEL templates.

## Included utilities

- `setup_jsonl_tracing` wires OpenTelemetry to the JSONL exporter (`otel_span_template.jsonl` compatible).
- `run_llm_with_span` / `run_tool_with_span` wrap LangChain calls and automatically populate `gen_ai.operation.name`, token usage, and `communication.*` byte counts.
- `invoke_agent_span` + `record_invoke_agent_output` capture `invoke_agent` orchestration steps in the same schema.
- `AgentRetryTrigger` / `AgentFailureCategory` enumerate the canonical strings for the MAS agent-outcome attributes so every benchmark shares the same vocabulary.
- `AgentCallContext` + `set_agent_*` helpers annotate spans with `agent.retry.*`, `agent.failure.*`, and `agent.output.useless*`.
- `PsutilMetricsRecorder` emits CPU/RSS snapshots that match `otel_metrics_template.jsonl`.

## Emitting MAS agent-outcome metrics

Every LangGraph benchmark should record retry/failure/useless metadata to make dashboards comparable. The template and constants in this module keep the values consistent:

| Attribute | Helper | Notes |
| --- | --- | --- |
| `agent.retry.attempt_number` / `.trigger` / `.previous_span_id` / `.reason` | `AgentRetryTrigger`, `set_agent_retry_attributes`, `AgentCallContext["retry"]` | Use `AgentRetryTrigger` values (`quality`, `relevance_guard`, `guard_fail`, `timeout`, `system`, `upstream`) instead of free-form strings. |
| `agent.failure.category` / `.reason` | `AgentFailureCategory`, `set_agent_failure_attributes`, `AgentCallContext["failure"]` | Map guardrail breaches to `guard`/`quality`, infrastructure errors to `system`/`timeout`/`upstream`. |
| `agent.output.useless` / `.reason` | `set_agent_usefulness`, `AgentCallContext["useless"]` | Flag call spans that produced no progress. |

### Recommended pattern

1. Store retry counters and prior span IDs inside the LangGraph state (e.g., `state["expand_retry_attempts"]`).
2. Pass an `AgentCallContext` into `run_llm_with_span(..., agent_context=context)` so the helper sets the retry/failure/useless attributes automatically.
3. Use the exported setter functions (`set_agent_failure_attributes`, `set_agent_usefulness`) from post-processing callbacks when you derive the final usefulness outcome.

Following this pattern ensures future benchmarks inherit the same OTEL naming scheme without duplicating boilerplate.
