"""
Shared OpenTelemetry helpers for LangGraph-based benchmarks.

Key utilities:
- `setup_jsonl_tracing` wires OTEL spans into a JSONL exporter that matches
  `otel_template/otel_span_template.json`.
- `run_llm_with_span` / `run_tool_with_span` wrap LangChain LLMs + tools and
  automatically capture `gen_ai.operation.name`, token usage, and
  `communication.*` byte counts.
- `invoke_agent_span` / `record_invoke_agent_output` provide a minimal,
  reusable way to tag LangGraph orchestration steps (`invoke_agent`) without
  duplicating bookkeeping.
- `PsutilMetricsRecorder` logs CPU/RSS samples using the metrics template.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple

try:  # pragma: no cover - psutil may be absent
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.version import __version__ as otel_sdk_version

try:  # pragma: no cover - optional dependency
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover
    BaseCallbackHandler = None  # type: ignore

if BaseCallbackHandler is None:  # pragma: no cover - fallback shim
    class BaseCallbackHandler:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

DEFAULT_ENVIRONMENT = os.getenv("DEPLOYMENT_ENVIRONMENT", "local")


class JsonlSpanExporter(SpanExporter):
    """Writes OpenTelemetry spans to JSONL using the shared template layout."""

    def __init__(
        self,
        destination: Path,
        resource_attributes: dict[str, Any],
        default_agent_name: str,
    ) -> None:
        self.destination = destination
        self.resource_attributes = resource_attributes
        self.default_agent_name = default_agent_name

    def export(self, spans: Iterable[Any]) -> SpanExportResult:
        lines: list[str] = []
        for span in spans:
            context = span.context
            parent_context = span.parent
            parent_id: str | None = None
            if parent_context and getattr(parent_context, "span_id", 0):
                parent_id = format(parent_context.span_id, "016x")

            attributes = dict(span.attributes or {})
            attributes.setdefault("gen_ai.operation.name", "unknown")
            agent_name = attributes.pop("agent.name", None) or self.default_agent_name

            communication_attrs: dict[str, Any] = {}
            for key in list(attributes.keys()):
                if key.startswith("communication."):
                    _, sub_key = key.split(".", 1)
                    communication_attrs[sub_key] = attributes.pop(key)

            record: dict[str, Any] = {
                "trace_id": format(context.trace_id, "032x"),
                "span_id": format(context.span_id, "016x"),
                "name": span.name,
                "agent_name": agent_name,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "duration_ns": span.end_time - span.start_time,
                "status": {
                    "status_code": span.status.status_code.name,
                    "description": span.status.description,
                },
                "attributes": attributes,
                "resource": {"attributes": self.resource_attributes},
            }
            if parent_id:
                record["parent_span_id"] = parent_id
            record["communication"] = {
                "is_in_process_call": bool(communication_attrs.get("is_in_process_call", False)),
                "input_message_size_bytes": int(
                    communication_attrs.get("input_message_size_bytes", 0)
                ),
                "output_message_size_bytes": int(
                    communication_attrs.get("output_message_size_bytes", 0)
                ),
                "total_message_size_bytes": int(
                    communication_attrs.get("total_message_size_bytes", 0)
                ),
            }

            events_payload: list[dict[str, Any]] = []
            for event in span.events or []:
                events_payload.append(
                    {
                        "name": event.name,
                        "timestamp": event.timestamp,
                        "attributes": dict(event.attributes or {}),
                    }
                )
            if events_payload:
                record["events"] = events_payload
            lines.append(json.dumps(record))

        with self.destination.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to clean up
        return None


def setup_jsonl_tracing(
    *,
    app_name: str,
    service_name: str,
    service_version: str,
    log_dir: Path,
    run_id: str,
    environment: str | None = None,
    set_global_provider: bool = True,
) -> tuple[trace.Tracer, Path, TracerProvider]:
    """
    Configure an OpenTelemetry tracer provider that writes spans to JSONL,
    returning the tracer, destination path, and provider handle.
    """

    log_dir.mkdir(parents=True, exist_ok=True)
    trace_path = log_dir / f"run_{run_id}.otel.jsonl"
    trace_path.touch(exist_ok=True)

    resource_attributes = {
        "service.name": service_name,
        "service.version": service_version,
        "deployment.environment": environment or DEFAULT_ENVIRONMENT,
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": otel_sdk_version,
    }
    resource = Resource.create(resource_attributes)
    provider = TracerProvider(resource=resource)
    exporter = JsonlSpanExporter(trace_path, dict(resource.attributes), app_name)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    if set_global_provider:
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer(app_name)
    else:
        tracer = provider.get_tracer(app_name)
    return tracer, trace_path, provider


class PsutilMetricsRecorder:
    """
    Periodically records process CPU and RSS memory metrics to JSONL.
    """

    def __init__(
        self,
        *,
        service_name: str,
        service_version: str,
        run_id: str,
        output_dir: Path,
        environment: str | None = None,
        scope: str | None = None,
        interval_seconds: float = 15.0,
        logger: Optional[Any] = None,
    ) -> None:
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment or DEFAULT_ENVIRONMENT
        self.scope = scope or f"{service_name}.system-metrics"
        self.interval_seconds = max(1.0, interval_seconds)
        self.logger = logger
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / f"{service_name}_{run_id}.metrics.jsonl"
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._process = self._init_process()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.debug(
                "PsutilMetricsRecorder writing to %s (%.1fs interval)",
                self.output_path,
                self.interval_seconds,
            )

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join()
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._write_snapshot()
            except Exception as exc:  # pragma: no cover - defensive logging
                if self.logger:
                    self.logger.warning(
                        "PsutilMetricsRecorder failed to write snapshot: %s", exc
                    )
            finally:
                self._stop_event.wait(self.interval_seconds)

    def _write_snapshot(self) -> None:
        timestamp = datetime.now(timezone.utc)
        timestamp_ns = time.time_ns()
        metrics_payload = [
            self._metric_entry(
                metric_name="process.cpu.usage",
                description="Process CPU usage percentage",
                unit="%",
                value=self._read_cpu_percent(),
                timestamp=timestamp,
                timestamp_ns=timestamp_ns,
            ),
            self._metric_entry(
                metric_name="process.memory.usage_bytes",
                description="Process memory usage in bytes",
                unit="bytes",
                value=self._read_memory_rss(),
                timestamp=timestamp,
                timestamp_ns=timestamp_ns,
            ),
        ]
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(metrics_payload) + "\n")

    def _metric_entry(
        self,
        *,
        metric_name: str,
        description: str,
        unit: str,
        value: float,
        timestamp: datetime,
        timestamp_ns: int,
    ) -> dict[str, Any]:
        return {
            "timestamp": timestamp.isoformat(),
            "metric_name": metric_name,
            "description": description,
            "unit": unit,
            "data_points": [
                {
                    "value": value,
                    "timestamp": timestamp_ns,
                    "attributes": {"agent.name": self.service_name},
                }
            ],
            "resource": {
                "attributes": {
                    "service.name": self.service_name,
                    "service.version": self.service_version,
                    "deployment.environment": self.environment,
                    "telemetry.sdk.name": "opentelemetry",
                    "telemetry.sdk.language": "python",
                    "telemetry.sdk.version": otel_sdk_version,
                }
            },
            "scope": self.scope,
        }

    def _read_cpu_percent(self) -> float:
        process = self._process
        if process is None:
            return 0.0
        try:
            return float(process.cpu_percent(interval=None))
        except Exception:  # pragma: no cover
            return 0.0

    def _read_memory_rss(self) -> float:
        process = self._process
        if process is None:
            return 0.0
        try:
            return float(process.memory_info().rss)
        except Exception:  # pragma: no cover
            return 0.0

    def _init_process(self):
        if psutil is None:
            return None
        try:
            process = psutil.Process()
            # Prime cpu_percent so subsequent calls return deltas instead of 0.0.
            process.cpu_percent(interval=None)
            return process
        except Exception:
            return None


def _byte_length(value: object, _visited: Optional[set[int]] = None) -> int:
    """
    Approximate UTF-8 payload size for OTEL communication metrics.

    Dict entries add both key + value sizes so downstream dashboards can reason
    about structured tool payloads the same way they were previously computed
    inside individual benchmarks.
    """

    if _visited is None:
        _visited = set()

    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (int, float, bool)):
        return len(str(value).encode("utf-8"))
    if isinstance(value, (list, tuple, set)):
        obj_id = id(value)
        if obj_id in _visited:
            return 0
        _visited.add(obj_id)
        return sum(_byte_length(item, _visited) for item in value)
    if isinstance(value, dict):
        obj_id = id(value)
        if obj_id in _visited:
            return 0
        _visited.add(obj_id)
        total = 0
        for key, val in value.items():
            total += _byte_length(key, _visited)
            total += _byte_length(val, _visited)
        return total
    if hasattr(value, "model_dump"):
        obj_id = id(value)
        if obj_id in _visited:
            return 0
        _visited.add(obj_id)
        try:
            return _byte_length(value.model_dump(), _visited)
        except Exception:  # pragma: no cover - best effort
            return 0
    if hasattr(value, "dict"):
        obj_id = id(value)
        if obj_id in _visited:
            return 0
        _visited.add(obj_id)
        try:
            return _byte_length(value.dict(), _visited)
        except Exception:  # pragma: no cover - best effort
            return 0
    if hasattr(value, "__dict__"):
        obj_id = id(value)
        if obj_id in _visited:
            return 0
        _visited.add(obj_id)
        return _byte_length(value.__dict__, _visited)
    if hasattr(value, "content"):
        return _byte_length(value.content, _visited)
    try:
        return len(str(value).encode("utf-8"))
    except Exception:  # pragma: no cover - best effort
        return 0


def estimate_message_bytes(value: object) -> int:
    """Public shim used by callers to keep `communication.*` math consistent."""

    return _byte_length(value)


def _append_callback(config: Optional[dict[str, Any]], callback) -> dict[str, Any]:
    new_config = dict(config or {})
    callbacks_entry = new_config.get("callbacks")

    if callbacks_entry is None:
        new_config["callbacks"] = [callback]
        return new_config

    if isinstance(callbacks_entry, (list, tuple)):
        callbacks_list = list(callbacks_entry)
        callbacks_list.append(callback)
        new_config["callbacks"] = callbacks_list
        return new_config

    # LangChain's CallbackManager exposes add_handler/copy.
    add_handler = getattr(callbacks_entry, "add_handler", None)
    if callable(add_handler):
        if hasattr(callbacks_entry, "copy"):
            try:
                manager = callbacks_entry.copy()
            except Exception:  # pragma: no cover - fall back to original
                manager = callbacks_entry
        else:
            manager = callbacks_entry
        manager.add_handler(callback)
        new_config["callbacks"] = manager
        return new_config

    # Fallback: wrap unknown types alongside the new callback.
    new_config["callbacks"] = [callbacks_entry, callback]
    return new_config


class LangChainUsageCallback(BaseCallbackHandler):  # type: ignore[misc]
    """Aggregates token usage + message sizes for LangChain LLM calls."""

    def __init__(self) -> None:
        super().__init__()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.input_bytes = 0
        self.output_bytes = 0
        self.llm_calls = 0
        self._recorded_responses: set[int] = set()

    def on_chat_model_start(self, serialized, messages, **kwargs):  # type: ignore[override]
        self.input_bytes += _byte_length(messages)

    def on_llm_start(self, serialized, prompts, **kwargs):  # type: ignore[override]
        self.input_bytes += _byte_length(prompts)

    def on_chat_model_end(self, response, **kwargs):  # type: ignore[override]
        self._record_generation_bytes(response)

    def on_llm_end(self, response, **kwargs):  # type: ignore[override]
        self.llm_calls += 1
        self._record_generation_bytes(response)
        llm_output = getattr(response, "llm_output", None)
        usage = None
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion_tokens = (
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
            total_tokens = usage.get("total_tokens") or usage.get("total_token_count")
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens
            self.prompt_tokens += int(prompt_tokens)
            self.completion_tokens += int(completion_tokens)
            self.total_tokens += int(total_tokens)

    def _record_generation_bytes(self, response) -> None:
        response_id = id(response)
        if response_id in self._recorded_responses:
            return
        self._recorded_responses.add(response_id)
        generations = getattr(response, "generations", None)
        if not generations:
            return
        for batch in generations:
            for generation in batch:
                if hasattr(generation, "text") and generation.text:
                    self.output_bytes += _byte_length(generation.text)
                elif hasattr(generation, "message"):
                    self.output_bytes += _byte_length(generation.message)


def record_usage_on_span(span, usage_callback: LangChainUsageCallback | None) -> None:
    if span is None or usage_callback is None:
        return
    span.set_attribute("gen_ai.usage.input_tokens", usage_callback.prompt_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", usage_callback.completion_tokens)
    span.set_attribute("gen_ai.usage.total_tokens", usage_callback.total_tokens)
    span.set_attribute("gen_ai.llm.call.count", usage_callback.llm_calls)
    # All measurements map to communication.* attributes per otel_span_template.json.
    span.set_attribute(
        "communication.input_message_size_bytes",
        usage_callback.input_bytes,
    )
    span.set_attribute(
        "communication.output_message_size_bytes",
        usage_callback.output_bytes,
    )
    span.set_attribute(
        "communication.total_message_size_bytes",
        usage_callback.input_bytes + usage_callback.output_bytes,
    )


def run_llm_with_span(
    tracer,
    span_name: str,
    *,
    agent_name: str,
    phase: str,
    config: Optional[dict[str, Any]],
    invoke_fn: Callable[[dict[str, Any]], Any],
    extra_attributes: Optional[dict[str, Any]] = None,
    in_process_call: bool = False,
):
    """
    Wrap a LangChain LLM invocation in an OTEL span.

    Ensures `gen_ai.operation.name` is `call_llm`, records LangChain token usage,
    and annotates the span with `communication.*` metrics as described in
    `otel_span_template.json`. Prefer this helper over bespoke span logic so
    every LangGraph example emits consistent OTEL attributes.
    """

    usage_callback = LangChainUsageCallback()
    lam_config = _append_callback(config, usage_callback)
    attributes = {
        "agent.name": agent_name,
        "gen_ai.operation.name": "call_llm",
        "langgraph.phase": phase,
    }
    if extra_attributes:
        attributes.update(extra_attributes)
    with tracer.start_as_current_span(span_name, attributes=attributes) as span:
        span.set_attribute("communication.is_in_process_call", in_process_call)
        result = invoke_fn(lam_config)
        record_usage_on_span(span, usage_callback)
        return result


def run_tool_with_span(
    tracer,
    span_name: str,
    *,
    agent_name: str,
    tool_name: str,
    payload: Any,
    invoke_fn: Callable[[Any, Optional[dict[str, Any]]], Any],
    config: Optional[dict[str, Any]] = None,
    extra_attributes: Optional[dict[str, Any]] = None,
    in_process_call: bool = False,
) -> Any:
    """
    Wrap a tool/MCP invocation in an OTEL span with `gen_ai.operation.name`
    locked to `execute_tool` and `communication.*` sizes derived from the
    serialized payload/result. Use this helper whenever a LangGraph node
    interacts with Tavily/MCP/etc. so downstream dashboards can compare tools.
    """

    attributes = {
        "agent.name": agent_name,
        "gen_ai.operation.name": "execute_tool",
        "tool.name": tool_name,
    }
    if extra_attributes:
        attributes.update(extra_attributes)

    with tracer.start_as_current_span(span_name, attributes=attributes) as span:
        span.set_attribute("communication.is_in_process_call", in_process_call)
        input_bytes = _byte_length(payload)
        span.set_attribute("communication.input_message_size_bytes", input_bytes)
        result = invoke_fn(payload, config)
        output_bytes = _byte_length(result)
        span.set_attribute("communication.output_message_size_bytes", output_bytes)
        span.set_attribute(
            "communication.total_message_size_bytes", input_bytes + output_bytes
        )
        return result


@contextmanager
def invoke_agent_span(
    tracer,
    span_name: str,
    *,
    agent_name: str,
    payload: Any | None = None,
    in_process_call: bool = True,
    extra_attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Tuple[Any, int]]:
    """
    Context manager for LangGraph invoke_agent spans.

    Returns the OTEL span plus the number of bytes derived from `payload`. Call
    `record_invoke_agent_output` later with the same byte count to update the
    `communication.total_message_size_bytes` field.
    """

    if tracer is None:
        yield None, 0
        return

    attributes = {
        "agent.name": agent_name,
        "gen_ai.operation.name": "invoke_agent",
    }
    if extra_attributes:
        attributes.update(extra_attributes)

    with tracer.start_as_current_span(span_name, attributes=attributes) as span:
        span.set_attribute("communication.is_in_process_call", in_process_call)
        input_bytes = 0
        if payload is not None:
            input_bytes = estimate_message_bytes(payload)
            span.set_attribute("communication.input_message_size_bytes", input_bytes)
            span.set_attribute("communication.total_message_size_bytes", input_bytes)
        yield span, input_bytes


def record_invoke_agent_output(span, output_value: Any, input_bytes: int) -> None:
    """Annotate invoke_agent spans with output sizes without duplicating logic."""

    if span is None:
        return
    output_bytes = estimate_message_bytes(output_value)
    span.set_attribute("communication.output_message_size_bytes", output_bytes)
    span.set_attribute("communication.total_message_size_bytes", input_bytes + output_bytes)


__all__ = [
    "JsonlSpanExporter",
    "PsutilMetricsRecorder",
    "setup_jsonl_tracing",
    "DEFAULT_ENVIRONMENT",
    "LangChainUsageCallback",
    "record_usage_on_span",
    "run_llm_with_span",
    "run_tool_with_span",
    "estimate_message_bytes",
    "invoke_agent_span",
    "record_invoke_agent_output",
]
