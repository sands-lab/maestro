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
- `AgentCallContext` + `set_agent_*` helpers annotate spans with the MAS
  agent-outcome attributes (`agent.retry.*`, `agent.failure.*`,
  `agent.output.useless*`) so new benchmarks can emit consistent telemetry by
  simply passing `agent_context=` into the span helpers.
- `AgentRetryTrigger` / `AgentFailureCategory` enumerate the canonical strings
  for `agent.retry.trigger` and `agent.failure.category` so dashboards can rely
  on a fixed vocabulary.
- `PsutilMetricsRecorder` logs CPU/RSS samples using the metrics template.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple, TypedDict

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
from opentelemetry.trace import Status, StatusCode

from ..process_metrics import CpuUsageSampler, read_rss_bytes_fallback

try:  # pragma: no cover - optional dependency
    from langchain_core.callbacks import BaseCallbackHandler
except Exception:  # pragma: no cover
    BaseCallbackHandler = None  # type: ignore

if BaseCallbackHandler is None:  # pragma: no cover - fallback shim
    class BaseCallbackHandler:  # type: ignore
        def __init__(self, *args, **kwargs):
            pass

DEFAULT_ENVIRONMENT = os.getenv("DEPLOYMENT_ENVIRONMENT", "local")


class AgentRetryTrigger:
    """Canonical `agent.retry.trigger` values shared across MAS benchmarks."""

    QUALITY = "quality"
    RELEVANCE_GUARD = "relevance_guard"
    GUARD_FAIL = "guard_fail"
    TIMEOUT = "timeout"
    SYSTEM = "system"
    UPSTREAM = "upstream"


class AgentFailureCategory:
    """Canonical `agent.failure.category` values."""

    GUARD = "guard"
    QUALITY = "quality"
    SYSTEM = "system"
    TIMEOUT = "timeout"
    UPSTREAM = "upstream"


class AgentRetryContext(TypedDict, total=False):
    """Retry metadata attached to spans via `agent.retry.*` attributes."""

    attempt_number: int
    trigger: str
    previous_span_id: str
    reason: str


class AgentFailureContext(TypedDict, total=False):
    """Failure metadata mapped to `agent.failure.*` attributes."""

    category: str
    reason: str


class AgentUselessContext(TypedDict, total=False):
    """Usefulness annotations mapped to `agent.output.useless*` attributes."""

    is_useless: bool
    reason: str


class AgentCallContext(TypedDict, total=False):
    """
    Bundle of optional agent outcome fields.

    Pass instances of this dict into `run_llm_with_span`, `run_tool_with_span`,
    or `invoke_agent_span` via the `agent_context` parameter so the helper can
    set `agent.retry.*`, `agent.failure.*`, and `agent.output.useless*`
    consistently across benchmarks.
    """

    retry: AgentRetryContext
    failure: AgentFailureContext
    useless: AgentUselessContext


def set_agent_retry_attributes(
    span,
    *,
    attempt_number: Optional[int] = None,
    trigger: Optional[str] = None,
    previous_span_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Populate the `agent.retry.*` attributes on the supplied span."""

    if span is None:
        return
    if attempt_number is not None:
        span.set_attribute("agent.retry.attempt_number", int(attempt_number))
    if trigger:
        span.set_attribute("agent.retry.trigger", trigger)
    if previous_span_id:
        span.set_attribute("agent.retry.previous_span_id", previous_span_id)
    if reason:
        span.set_attribute("agent.retry.reason", reason)


def set_agent_failure_attributes(
    span,
    *,
    category: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """Populate the `agent.failure.*` attributes on the supplied span."""

    if span is None:
        return
    if category:
        span.set_attribute("agent.failure.category", category)
    if reason:
        span.set_attribute("agent.failure.reason", reason)


def set_agent_usefulness(
    span,
    *,
    is_useless: bool,
    reason: Optional[str] = None,
) -> None:
    """Populate the `agent.output.useless*` attributes on the supplied span."""

    if span is None:
        return
    span.set_attribute("agent.output.useless", bool(is_useless))
    if reason:
        span.set_attribute("agent.output.useless_reason", reason)


def _apply_agent_context(span, agent_context: Optional[AgentCallContext]) -> None:
    if span is None or not agent_context:
        return
    retry_ctx = agent_context.get("retry")
    if retry_ctx:
        set_agent_retry_attributes(span, **retry_ctx)
    failure_ctx = agent_context.get("failure")
    if failure_ctx:
        set_agent_failure_attributes(span, **failure_ctx)
    useless_ctx = agent_context.get("useless")
    if useless_ctx:
        set_agent_usefulness(span, **useless_ctx)


def span_id_hex(span) -> str | None:
    """Return the current span_id encoded as 16-char hex."""

    if span is None:
        return None
    context = span.get_span_context()
    if not context:
        return None
    return format(context.span_id, "016x")


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
        self._cpu_sampler = CpuUsageSampler(self._process)

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
        return float(self._cpu_sampler.read_percent())

    def _read_memory_rss(self) -> float:
        process = self._process
        if process is not None:
            try:
                return float(process.memory_info().rss)
            except Exception:  # pragma: no cover
                pass
        return read_rss_bytes_fallback()

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
        self._recorded_usage: set[int] = set()

    def on_chat_model_start(self, serialized, messages, **kwargs):  # type: ignore[override]
        self.input_bytes += _byte_length(messages)

    def on_llm_start(self, serialized, prompts, **kwargs):  # type: ignore[override]
        self.input_bytes += _byte_length(prompts)

    def on_chat_model_end(self, response, **kwargs):  # type: ignore[override]
        self.llm_calls += 1
        self._record_generation_bytes(response)

    def on_llm_end(self, response, **kwargs):  # type: ignore[override]
        self.llm_calls += 1
        self._record_generation_bytes(response)
        llm_output = getattr(response, "llm_output", None)
        usage = None
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
            if usage is None and isinstance(llm_output.get("token_count"), dict):
                usage = llm_output.get("token_count")
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

    def _apply_usage_metadata(self, usage: dict | None) -> None:
        if not isinstance(usage, dict):
            return
        usage_id = id(usage)
        if usage_id in self._recorded_usage:
            return
        self._recorded_usage.add(usage_id)

        # Handle both usage_metadata (Gemini) and token_count (Vertex) shapes.
        token_count = usage.get("token_count")
        if isinstance(token_count, dict):
            prompt_tokens = int(
                token_count.get("prompt_tokens")
                or token_count.get("input_tokens")
                or token_count.get("input_token_count")
                or 0
            )
            completion_tokens = int(
                token_count.get("completion_tokens")
                or token_count.get("output_tokens")
                or token_count.get("output_token_count")
                or 0
            )
            total_tokens = token_count.get("total_tokens") or token_count.get("total_token_count")
        else:
            prompt_tokens = int(
                usage.get("prompt_token_count")
                or usage.get("prompt_tokens")
                or usage.get("input_tokens")
                or 0
            )
            completion_tokens = int(
                usage.get("candidates_token_count")
                or usage.get("completion_tokens")
                or usage.get("output_tokens")
                or 0
            )
            total_tokens = usage.get("total_token_count") or usage.get("total_tokens")

        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += int(total_tokens)

    def _record_generation_bytes(self, response) -> None:
        response_id = id(response)
        if response_id in self._recorded_responses:
            return
        self._recorded_responses.add(response_id)
        top_metadata = getattr(response, "response_metadata", None)
        if isinstance(top_metadata, dict):
            self._apply_usage_metadata(top_metadata.get("usage_metadata") or top_metadata)
        generations = getattr(response, "generations", None)
        if not generations:
            return
        for batch in generations:
            for generation in batch:
                if hasattr(generation, "text") and generation.text:
                    self.output_bytes += _byte_length(generation.text)
                elif hasattr(generation, "message"):
                    self.output_bytes += _byte_length(generation.message)
                metadata = getattr(getattr(generation, "message", None), "response_metadata", None)
                if isinstance(metadata, dict):
                    usage = metadata.get("usage_metadata") or metadata
                    self._apply_usage_metadata(usage)
                gen_info = getattr(generation, "generation_info", None)
                if isinstance(gen_info, dict):
                    self._apply_usage_metadata(gen_info.get("token_count"))


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
    agent_context: Optional[AgentCallContext] = None,
    postprocess_fn: Optional[Callable[[Any, Any], None]] = None,
):
    """
    Wrap a LangChain LLM invocation in an OTEL span.

    Ensures `gen_ai.operation.name` is `call_llm`, records LangChain token usage,
    and annotates the span with `communication.*` metrics as described in
    `otel_span_template.json`. Prefer this helper over bespoke span logic so
    every LangGraph example emits consistent OTEL attributes.

    Set `agent_context` to emit the MAS-specific agent outcome attributes
    (`agent.retry.*`, `agent.failure.*`, `agent.output.useless*`) without
    writing attribute names by hand. Most benchmarks pass a context that lives
    in LangGraph state so retries can share the same span lineage.
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
        if agent_context:
            _apply_agent_context(span, agent_context)
        try:
            result = invoke_fn(lam_config)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            failure_ctx = (agent_context or {}).get("failure")
            if not failure_ctx or not failure_ctx.get("reason"):
                set_agent_failure_attributes(
                    span,
                    category=(failure_ctx or {}).get("category") or "system",
                    reason=str(exc),
                )
            raise
        record_usage_on_span(span, usage_callback)
        if postprocess_fn:
            try:
                postprocess_fn(span, result)
            except Exception as exc:  # pragma: no cover - best effort logging
                span.add_event(
                    "agent.postprocess_error",
                    attributes={"agent.log": f"postprocess failed: {exc}"},
                )
        return result
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
    agent_context: Optional[AgentCallContext] = None,
    postprocess_fn: Optional[Callable[[Any, Any], None]] = None,
) -> Any:
    """
    Wrap a tool/MCP invocation in an OTEL span with `gen_ai.operation.name`
    locked to `execute_tool` and `communication.*` sizes derived from the
    serialized payload/result. Use this helper whenever a LangGraph node
    interacts with Tavily/MCP/etc. so downstream dashboards can compare tools.

    Provide `agent_context` to automatically stamp `agent.retry.*`,
    `agent.failure.*`, and `agent.output.useless*` attributes whenever a tool
    call is a retry, fails, or returns a useless payload.
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
        if agent_context:
            _apply_agent_context(span, agent_context)
        input_bytes = _byte_length(payload)
        span.set_attribute("communication.input_message_size_bytes", input_bytes)
        try:
            result = invoke_fn(payload, config)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            failure_ctx = (agent_context or {}).get("failure")
            if not failure_ctx or not failure_ctx.get("reason"):
                set_agent_failure_attributes(
                    span,
                    category=(failure_ctx or {}).get("category") or "system",
                    reason=str(exc),
                )
            raise
        output_bytes = _byte_length(result)
        span.set_attribute("communication.output_message_size_bytes", output_bytes)
        span.set_attribute(
            "communication.total_message_size_bytes", input_bytes + output_bytes
        )
        if postprocess_fn:
            try:
                postprocess_fn(span, result)
            except Exception as exc:  # pragma: no cover - best effort logging
                span.add_event(
                    "agent.postprocess_error",
                    attributes={"agent.log": f"postprocess failed: {exc}"},
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
    agent_context: Optional[AgentCallContext] = None,
) -> Iterator[Tuple[Any, int]]:
    """
    Context manager for LangGraph invoke_agent spans.

    Returns the OTEL span plus the number of bytes derived from `payload`. Call
    `record_invoke_agent_output` later with the same byte count to update the
    `communication.total_message_size_bytes` field.

    Pass `agent_context` to propagate retry/failure/useless metadata down to
    child spans when orchestration steps are retried or deemed useless.
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
        if agent_context:
            _apply_agent_context(span, agent_context)
        if agent_context:
            _apply_agent_context(span, agent_context)
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


def check_timeout(start_time: float, timeout_seconds: Optional[float]) -> None:
    """Raise TimeoutError if elapsed time exceeds timeout_seconds."""

    if timeout_seconds is None:
        return
    if (time.perf_counter() - start_time) > timeout_seconds:
        raise TimeoutError("run_timeout_seconds_exceeded")


def set_run_outcome(span, *, success: bool, reason: Optional[str] = None) -> None:
    """Populate run outcome attributes on a span."""

    if span is None:
        return
    span.set_attribute("run.outcome", "success" if success else "failure")
    if reason:
        span.set_attribute("run.outcome_reason", reason)


def _normalize_answer(text: str) -> str:
    """Lowercase and strip punctuation/stopwords for token-level matching."""

    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_f1_and_em(pred: str, gold: str) -> tuple[float, float]:
    """Token-level F1 + exact match with Hotpot-style normalization."""

    pred_tokens = _normalize_answer(pred).split()
    gold_tokens = _normalize_answer(gold).split()
    em = float(pred_tokens == gold_tokens)
    if not pred_tokens and not gold_tokens:
        return 1.0, 1.0
    if not pred_tokens or not gold_tokens:
        return 0.0, em
    gold_counts: dict[str, int] = {}
    for token in gold_tokens:
        gold_counts[token] = gold_counts.get(token, 0) + 1
    num_same = 0
    for token in pred_tokens:
        if gold_counts.get(token, 0) > 0:
            num_same += 1
            gold_counts[token] -= 1
    if num_same == 0:
        return 0.0, em
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return f1, em


def judge_with_f1(
    pred: Optional[str],
    gold: Optional[str],
    *,
    correct_threshold: float = 0.7,
    wrong_threshold: float = 0.5,
) -> tuple[str, str]:
    """
    Return (judgement, reason) where judgement is correct|wrong|unknown based on F1/EM.
    """

    if not gold:
        return "unknown", "no_gold"
    if pred is None or pred == "":
        return "wrong", "empty_prediction"
    f1, em = token_f1_and_em(pred, gold)
    if f1 >= correct_threshold:
        return "correct", f"f1={f1:.2f} em={em:.2f}"
    if f1 <= wrong_threshold:
        return "wrong", f"f1={f1:.2f} em={em:.2f}"
    return "unknown", f"borderline f1={f1:.2f} em={em:.2f}"


def _parse_llm_judgement(text: str) -> tuple[Optional[str], Optional[str]]:
    lowered = text.lower()
    label: Optional[str] = None
    if "correct" in lowered:
        label = "correct"
    if "wrong" in lowered or "incorrect" in lowered:
        label = "wrong" if label is None else label
    if "unknown" in lowered:
        label = "unknown"
    try:
        data = json.loads(text)
        candidate = data.get("judgement") if isinstance(data, dict) else None
        if candidate in {"correct", "wrong", "unknown"}:
            label = candidate
        reason = data.get("reason") if isinstance(data, dict) else None
        return label, reason
    except Exception:
        return label, None


def llm_judge_answer(
    llm,
    *,
    question: Optional[str],
    pred: Optional[str],
    gold: Optional[str],
) -> tuple[str, str]:
    """
    Use an LLM to judge correctness relative to the HotpotQA gold answer.

    The LLM must support `.invoke(messages)` where messages are LangChain-style
    `SystemMessage` / `HumanMessage`. Returns (judgement, reason).
    """

    if not gold:
        return "unknown", "no_gold"
    if pred is None or pred == "":
        return "wrong", "empty_prediction"
    if llm is None:
        return "unknown", "llm_unavailable"
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
    except Exception:
        return "unknown", "langchain_messages_missing"

    prompt = [
        SystemMessage(
            content=(
                "You are a strict HotpotQA judge. Decide whether the model answer "
                "matches the gold answer. Respond with a JSON object containing "
                "\"judgement\" (correct|wrong|unknown) and \"reason\" (short). "
                "Use unknown if you are unsure."
            )
        ),
        HumanMessage(
            content=(
                f"Question: {question or '<unknown>'}\n"
                f"Gold answer: {gold}\n"
                f"Model answer: {pred}\n"
                "Return JSON: {\"judgement\": \"correct|wrong|unknown\", \"reason\": \"...\"}"
            )
        ),
    ]
    try:
        response = llm.invoke(prompt)
    except Exception as exc:  # pragma: no cover - network/model errors
        return "unknown", f"llm_error:{exc}"
    text = getattr(response, "content", None) or str(response)
    label, reason = _parse_llm_judgement(text)
    if label not in {"correct", "wrong", "unknown"}:
        label = "unknown"
    if not reason:
        reason = text.strip()[:240]
    return label, reason


def evaluate_answer(
    *,
    mode: str,
    pred: Optional[str],
    gold: Optional[str],
    question: Optional[str] = None,
    llm: Any = None,
    correct_threshold: float = 0.7,
    wrong_threshold: float = 0.5,
) -> tuple[str, str]:
    """Dispatch to F1 or LLM judge; always returns a single judgement + reason."""

    if mode == "llm":
        return llm_judge_answer(
            llm,
            question=question,
            pred=pred,
            gold=gold,
        )
    return judge_with_f1(
        pred,
        gold,
        correct_threshold=correct_threshold,
        wrong_threshold=wrong_threshold,
    )


def record_run_judgement(span, judgement: str, reason: Optional[str]) -> None:
    """Set `run.judgement*` attributes on a span."""

    if span is None:
        return
    span.set_attribute("run.judgement", judgement)
    if reason:
        span.set_attribute("run.judgement_reason", reason)


__all__ = [
    "JsonlSpanExporter",
    "PsutilMetricsRecorder",
    "setup_jsonl_tracing",
    "DEFAULT_ENVIRONMENT",
    "AgentRetryTrigger",
    "AgentFailureCategory",
    "AgentRetryContext",
    "AgentFailureContext",
    "AgentUselessContext",
    "AgentCallContext",
    "set_agent_retry_attributes",
    "set_agent_failure_attributes",
    "set_agent_usefulness",
    "span_id_hex",
    "LangChainUsageCallback",
    "record_usage_on_span",
    "run_llm_with_span",
    "run_tool_with_span",
    "estimate_message_bytes",
    "invoke_agent_span",
    "record_invoke_agent_output",
    "check_timeout",
    "set_run_outcome",
    "token_f1_and_em",
    "judge_with_f1",
    "llm_judge_answer",
    "evaluate_answer",
    "record_run_judgement",
]
