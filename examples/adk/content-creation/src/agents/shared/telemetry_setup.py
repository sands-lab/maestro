# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OpenTelemetry setup with local JSON file export support."""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from opentelemetry import trace, metrics
from opentelemetry.trace import SpanKind
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    ConsoleMetricExporter,
    MetricExporter,
    MetricExportResult,
)
from opentelemetry.sdk.resources import Resource
import socket

logger = logging.getLogger(__name__)

# Global variable to track if metrics are initialized
_metrics_initialized = False
_metrics_thread = None


class JsonFileMetricExporter(MetricExporter):
    """Exporter that writes metrics to local JSON files."""

    def __init__(self, file_path: str):
        """Initialize JSON file exporter.

        Args:
            file_path: Path to JSON file
        """
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # Set preferred temporality and aggregation (required by PeriodicExportingMetricReader)
        # Use None to accept defaults (same as ConsoleMetricExporter)
        self._preferred_temporality = None
        self._preferred_aggregation = None

    def export(self, metrics_data, timeout_millis: float = 10000, **kwargs):
        """Export metrics to JSON file.

        Args:
            metrics_data: Metrics data to export
            timeout_millis: Maximum time to wait for export (not used for file export)
            **kwargs: Additional arguments

        Returns:
            MetricExportResult.SUCCESS
        """
        try:
            # Convert metrics to serializable format
            metric_records = []

            # Extract metric data from the metrics_data object
            if hasattr(metrics_data, 'resource_metrics'):
                for resource_metric in metrics_data.resource_metrics:
                    resource_attrs = dict(resource_metric.resource.attributes) if resource_metric.resource and resource_metric.resource.attributes else {}

                    for scope_metric in resource_metric.scope_metrics:
                        scope_name = scope_metric.scope.name if scope_metric.scope else "unknown"

                        for metric in scope_metric.metrics:
                            metric_name = metric.name
                            metric_description = metric.description if hasattr(metric, 'description') else None
                            metric_unit = metric.unit if hasattr(metric, 'unit') else None

                            # Extract data points
                            data_points = []
                            if hasattr(metric, 'data') and hasattr(metric.data, 'data_points'):
                                for data_point in metric.data.data_points:
                                    point_dict = {
                                        "value": data_point.value if hasattr(data_point, 'value') else None,
                                        "timestamp": data_point.time_unix_nano if hasattr(data_point, 'time_unix_nano') else None,
                                        "attributes": dict(data_point.attributes) if hasattr(data_point, 'attributes') and data_point.attributes else {},
                                    }
                                    data_points.append(point_dict)

                            metric_record = {
                                "timestamp": datetime.now().isoformat(),
                                "metric_name": metric_name,
                                "description": metric_description,
                                "unit": metric_unit,
                                "data_points": data_points,
                                "resource": {
                                    "attributes": resource_attrs,
                                },
                                "scope": scope_name,
                            }
                            metric_records.append(metric_record)

            # Append to file (supports incremental writes)
            if metric_records:
                with open(self.file_path, "a", encoding="utf-8") as f:
                    for record in metric_records:
                        f.write(json.dumps(record, default=str) + "\n")

            return MetricExportResult.SUCCESS
        except Exception as e:
            logger.error(f"Failed to export metrics to {self.file_path}: {e}", exc_info=True)
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30000):
        """Force flush any pending metrics.

        Args:
            timeout_millis: Maximum time to wait for flush

        Returns:
            True if flush succeeded, False otherwise
        """
        return True

    def shutdown(self, timeout_millis: float = 30000, **kwargs):
        """Shutdown the exporter.

        Args:
            timeout_millis: Maximum time to wait for shutdown
            **kwargs: Additional arguments
        """
        pass


class JsonFileSpanExporter(SpanExporter):
    """Exporter that writes spans to local JSON files."""

    def __init__(self, file_path: str):
        """Initialize JSON file exporter.

        Args:
            file_path: Path to JSON file
        """
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.spans = []

    def export(self, spans):
        """Export spans to JSON file.

        Args:
            spans: List of spans to export

        Returns:
            SpanExportResult.SUCCESS
        """
        try:
            # Convert spans to serializable format
            span_data = []
            for span in spans:
                # Get parent span ID if exists
                parent_span_id = None
                if hasattr(span, "parent") and span.parent:
                    parent_span_id = format(span.parent.span_id, "016x")
                elif hasattr(span, "parent_context") and span.parent_context:
                    parent_span_id = format(span.parent_context.span_id, "016x")

                # Extract agent name from span attributes or name for better identification
                attributes = dict(span.attributes) if span.attributes else {}
                agent_name = (
                    attributes.get("gen_ai.agent.name") or
                    attributes.get("adk.agent.name") or
                    (span.name.split()[-1] if "invoke_agent" in span.name and len(span.name.split()) > 1 else None) or
                    "unknown"
                )

                # Add agent_name to attributes if not already present (for backward compatibility)
                if "agent.name" not in attributes and agent_name != "unknown":
                    attributes["agent.name"] = agent_name

                # Ensure gen_ai.agent.name is present (required by template)
                # For call_llm spans, try to extract from gcp.vertex.agent.llm_request labels
                if "gen_ai.agent.name" not in attributes:
                    # For call_llm spans, try to get from llm_request labels
                    if "call_llm" in span.name.lower():
                        llm_request = attributes.get("gcp.vertex.agent.llm_request", "")
                        if llm_request and isinstance(llm_request, str):
                            try:
                                llm_req_data = json.loads(llm_request)
                                if "config" in llm_req_data and "labels" in llm_req_data["config"]:
                                    adk_agent_name = llm_req_data["config"]["labels"].get("adk_agent_name")
                                    if adk_agent_name:
                                        attributes["gen_ai.agent.name"] = adk_agent_name
                                        # Also update agent_name if it was "unknown"
                                        if agent_name == "unknown":
                                            agent_name = adk_agent_name
                            except (json.JSONDecodeError, KeyError, TypeError):
                                pass

                    # If still not found, use extracted agent_name or agent.name
                    if "gen_ai.agent.name" not in attributes:
                        if agent_name != "unknown":
                            attributes["gen_ai.agent.name"] = agent_name
                        elif "agent.name" in attributes:
                            attributes["gen_ai.agent.name"] = attributes["agent.name"]
                        else:
                            # Field must exist per template, set to null if not available
                            attributes["gen_ai.agent.name"] = None

                # Ensure LLM usage fields are present (default to 0 if missing)
                if "gen_ai.usage.input_tokens" not in attributes:
                    attributes["gen_ai.usage.input_tokens"] = 0
                if "gen_ai.usage.output_tokens" not in attributes:
                    attributes["gen_ai.usage.output_tokens"] = 0
                if "gen_ai.usage.total_tokens" not in attributes:
                    # Calculate total if not present
                    input_tokens = attributes.get("gen_ai.usage.input_tokens", 0) or 0
                    output_tokens = attributes.get("gen_ai.usage.output_tokens", 0) or 0
                    attributes["gen_ai.usage.total_tokens"] = input_tokens + output_tokens

                # Add LLM/MCP call counts (default to 0 if missing)
                if "gen_ai.llm.call.count" not in attributes:
                    # Set to 1 if this is a call_llm span, otherwise 0
                    attributes["gen_ai.llm.call.count"] = 1 if "call_llm" in span.name.lower() else 0
                if "gen_ai.mcp.call.count" not in attributes:
                    attributes["gen_ai.mcp.call.count"] = 0

                # Ensure gen_ai.operation.name is present (required by template)
                if "gen_ai.operation.name" not in attributes:
                    if "call_llm" in span.name.lower():
                        attributes["gen_ai.operation.name"] = "call_llm"
                    elif "invoke_agent" in span.name.lower():
                        attributes["gen_ai.operation.name"] = "invoke_agent"
                    elif "execute_tool" in span.name.lower():
                        attributes["gen_ai.operation.name"] = "execute_tool"
                    elif span.name.lower() == "invocation":
                        # invocation spans are top-level entry points
                        attributes["gen_ai.operation.name"] = "invocation"

                # Ensure gen_ai.conversation.id is present (required by template)
                # Copy from session_id if available, otherwise set to null
                if "gen_ai.conversation.id" not in attributes:
                    session_id = attributes.get("gcp.vertex.agent.session_id")
                    if session_id:
                        attributes["gen_ai.conversation.id"] = session_id
                    else:
                        # Field must exist per template, set to null if not available
                        attributes["gen_ai.conversation.id"] = None

                # Add missing optional fields with default values (per template)
                # gen_ai.agent.description - optional, only in invoke_agent spans
                if "gen_ai.agent.description" not in attributes:
                    # Try to extract from system_instruction or other sources
                    # For now, set to empty string if not available
                    attributes["gen_ai.agent.description"] = ""

                # gen_ai.tool.* fields - only for execute_tool spans
                if "gen_ai.tool.name" not in attributes:
                    attributes["gen_ai.tool.name"] = ""
                if "gen_ai.tool.type" not in attributes:
                    attributes["gen_ai.tool.type"] = ""
                if "gen_ai.tool.call.id" not in attributes:
                    attributes["gen_ai.tool.call.id"] = ""
                if "gen_ai.tool.description" not in attributes:
                    attributes["gen_ai.tool.description"] = ""

                # gcp.vertex.agent.tool_* fields - optional
                if "gcp.vertex.agent.tool_call_args" not in attributes:
                    attributes["gcp.vertex.agent.tool_call_args"] = ""
                if "gcp.vertex.agent.tool_response" not in attributes:
                    attributes["gcp.vertex.agent.tool_response"] = ""

                # mcp.* fields - optional, not applicable for Vertex AI
                if "mcp.server" not in attributes:
                    attributes["mcp.server"] = ""
                if "mcp.tool" not in attributes:
                    attributes["mcp.tool"] = ""

                # agent.log - optional
                if "agent.log" not in attributes:
                    attributes["agent.log"] = ""

                # Note: CPU/memory metrics are collected separately in metrics JSONL file,
                # so we don't add them to span attributes (per template guidelines)

                # Calculate communication metrics for agent-to-agent calls
                communication_metrics = {}
                input_size = 0
                output_size = 0

                # Check if this is an agent-to-agent communication
                # In distributed systems, this could be AgentTool or HTTP calls between agents
                tool_type = attributes.get("gen_ai.tool.type", "")
                is_agent_tool = tool_type == "AgentTool"

                # For execute_tool spans: check if it's agent communication
                if "execute_tool" in span.name.lower():
                    # Check if it's AgentTool (in-process) or HTTP call to another agent (distributed)
                    if is_agent_tool:
                        # In-process agent-to-agent call
                        communication_metrics["is_agent_communication"] = True

                        # Input: tool call arguments
                        tool_call_args = attributes.get("gcp.vertex.agent.tool_call_args", "")
                        if tool_call_args and isinstance(tool_call_args, str) and tool_call_args != "{}":
                            input_size = len(tool_call_args.encode('utf-8'))

                        # Output: tool response
                        tool_response = attributes.get("gcp.vertex.agent.tool_response", "")
                        if tool_response and isinstance(tool_response, str) and tool_response != "{}":
                            output_size = len(tool_response.encode('utf-8'))
                    else:
                        # Could be HTTP call to another agent in distributed system
                        # Check if it's a send_message or similar agent communication tool
                        tool_name = attributes.get("gen_ai.tool.name", "").lower()
                        if "message" in tool_name or "agent" in tool_name:
                            communication_metrics["is_agent_communication"] = True

                            tool_call_args = attributes.get("gcp.vertex.agent.tool_call_args", "")
                            if tool_call_args and isinstance(tool_call_args, str) and tool_call_args != "{}":
                                input_size = len(tool_call_args.encode('utf-8'))

                            tool_response = attributes.get("gcp.vertex.agent.tool_response", "")
                            if tool_response and isinstance(tool_response, str) and tool_response != "{}":
                                output_size = len(tool_response.encode('utf-8'))

                # For call_llm spans: measure actual LLM request and response
                elif "call_llm" in span.name.lower():
                    # Input: LLM request
                    llm_request = attributes.get("gcp.vertex.agent.llm_request", "")
                    if llm_request and isinstance(llm_request, str) and llm_request != "{}":
                        input_size = len(llm_request.encode('utf-8'))

                    # Output: LLM response
                    llm_response = attributes.get("gcp.vertex.agent.llm_response", "")
                    if llm_response and isinstance(llm_response, str) and llm_response != "{}":
                        output_size = len(llm_response.encode('utf-8'))

                # Store communication metrics and add to attributes for template compliance
                # Template shows these fields, but per README: "If a field doesn't apply, omit it"
                # So we only add them when there's actual communication data
                # Ensure is_in_process_call is always present (default false)
                if "is_in_process_call" not in communication_metrics:
                    communication_metrics["is_in_process_call"] = False

                if input_size > 0:
                    communication_metrics["input_message_size_bytes"] = input_size
                    attributes["communication.input_message_size_bytes"] = input_size

                if output_size > 0:
                    communication_metrics["output_message_size_bytes"] = output_size
                    attributes["communication.output_message_size_bytes"] = output_size

                if input_size > 0 or output_size > 0:
                    communication_metrics["total_message_size_bytes"] = input_size + output_size
                    attributes["communication.total_message_size_bytes"] = input_size + output_size

                # Get span kind
                span_kind = "INTERNAL"  # Default
                if hasattr(span, "kind"):
                    try:
                        kind_value = span.kind if isinstance(span.kind, int) else span.kind.value
                        span_kind = SpanKind(kind_value).name if kind_value is not None else "INTERNAL"
                    except (ValueError, AttributeError):
                        span_kind = "INTERNAL"

                # Get resource attributes and add host.name if available
                resource_attrs = dict(span.resource.attributes) if span.resource and span.resource.attributes else {}
                if "host.name" not in resource_attrs:
                    try:
                        hostname = socket.gethostname()
                        if hostname:
                            resource_attrs["host.name"] = hostname
                    except Exception:
                        pass  # host.name is optional

                span_dict = {
                    "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                    "parent_span_id": parent_span_id,
                    "name": span.name,
                    "kind": span_kind,  # Add OTEL canonical kind
                    "agent_name": agent_name,  # Add explicit agent_name field
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "duration_ns": span.end_time - span.start_time if span.end_time and span.start_time else None,
                    "status": {
                        "status_code": span.status.status_code.name if span.status else None,
                        "description": span.status.description if span.status and span.status.description else "",
                    },
                    "attributes": attributes,
                    "communication": communication_metrics,  # Add communication metrics
                    "events": [
                        {
                            "name": event.name,
                            "timestamp": event.timestamp,
                            "attributes": dict(event.attributes) if event.attributes else {},
                        }
                        for event in span.events
                    ],
                    "resource": {
                        "attributes": resource_attrs,
                    },
                }
                span_data.append(span_dict)

            # Append to file (supports incremental writes)
            with open(self.file_path, "a", encoding="utf-8") as f:
                for span_dict in span_data:
                    f.write(json.dumps(span_dict, default=str) + "\n")

            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.error(f"Failed to export spans to {self.file_path}: {e}", exc_info=True)
            return SpanExportResult.FAILURE

    def shutdown(self):
        """Shutdown the exporter."""
        pass


def setup_tracing(service_name: Optional[str] = None, trace_file: Optional[str] = None):
    """Setup OpenTelemetry tracing with local JSON file export.

    This function configures OpenTelemetry to automatically collect telemetry data
    from ADK agents. The instrumentation is automatic - ADK and OpenTelemetry
    instrumentation libraries will automatically create spans for:

    - Agent invocations (invoke_agent)
    - LLM calls (call_llm) with model, tokens, and request/response data
    - Tool/function executions (execute_tool)
    - A2A server operations (request handling, event queue operations)
    - HTTP requests between agents

    The collected data includes:
    - Trace IDs and Span IDs for distributed tracing
    - Timing information (start time, end time, duration)
    - Status codes (OK, ERROR, UNSET)
    - Attributes (gen_ai.system, gen_ai.request.model, gen_ai.usage.*, etc.)
    - Events (exceptions, custom events)
    - Resource attributes (service.name, service.version, etc.)

    Args:
        service_name: Service name, defaults to OTEL_SERVICE_NAME env var or "adk-agent"
        trace_file: Trace file path, defaults to OTEL_TRACE_FILE env var or auto-generated
    """
    try:
        # Get configuration from environment variables
        service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "adk-agent")
        trace_file = trace_file or os.getenv("OTEL_TRACE_FILE")

        if not trace_file:
            # Default file path: traces directory in current working directory
            trace_dir = Path.cwd() / "traces"
            trace_dir.mkdir(exist_ok=True)
            # Use unified timestamp from environment variable if available (for distributed systems)
            # Otherwise, generate timestamp locally (for standalone agents)
            timestamp = os.getenv("OTEL_RUN_TIMESTAMP")
            if not timestamp:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_file = str(trace_dir / f"{service_name}_{timestamp}.jsonl")

        logger.info(f"Setting up OpenTelemetry tracing for {service_name}")
        logger.info(f"Trace file: {trace_file}")

        # Create resource
        resource = Resource.create({
            "service.name": service_name,
            "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
            "deployment.environment": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "local"),
        })

        # Check if tracer provider already exists
        current_provider = trace.get_tracer_provider()
        provider_already_set = isinstance(current_provider, TracerProvider)

        # If current provider is not a TracerProvider instance, create a new one
        if not provider_already_set:
            tracer_provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(tracer_provider)
        else:
            # Use existing provider but update resource
            tracer_provider = current_provider
            # Merge resource attributes
            if hasattr(tracer_provider, 'resource') and tracer_provider.resource:
                existing_attrs = dict(tracer_provider.resource.attributes) if tracer_provider.resource.attributes else {}
                new_attrs = dict(resource.attributes) if resource.attributes else {}
                merged_attrs = {**existing_attrs, **new_attrs}
                tracer_provider.resource = Resource.create(merged_attrs)
            else:
                tracer_provider.resource = resource

        # Create and add JSON file exporter
        json_exporter = JsonFileSpanExporter(trace_file)
        span_processor = SimpleSpanProcessor(json_exporter)
        tracer_provider.add_span_processor(span_processor)

        # Ensure tracer provider is set
        trace.set_tracer_provider(tracer_provider)

        # Set environment variables to enable instrumentation libraries
        os.environ.setdefault("OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED", "true")
        # Enable full message content capture for GenAI instrumentation
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
        # Enable HTTP instrumentation to capture request/response sizes
        os.environ.setdefault("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELD_NAMES", ".*")
        os.environ.setdefault("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST", ".*")
        os.environ.setdefault("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE", ".*")
        # Increase max attribute length to capture full messages (default is 250)
        os.environ.setdefault("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", "10000")

        # Try to enable ADK's telemetry (only succeeds if OTLP endpoint is set)
        # Even if it fails, instrumentation libraries will use our tracer provider
        otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otel_endpoint:
            try:
                from google.adk.telemetry.setup import maybe_set_otel_providers
                maybe_set_otel_providers()
                logger.info(f"ADK OTLP exporter enabled for {otel_endpoint}")
            except Exception as e:
                logger.warning(f"Failed to enable ADK OTLP exporter: {e}")

        logger.info("Tracer provider configured with JSON file exporter")

        logger.info("OpenTelemetry tracing setup completed")

    except Exception as e:
        logger.error(f"Failed to setup tracing: {e}", exc_info=True)
        # Don't raise exception, allow application to continue (tracing is optional)


def setup_metrics(service_name: Optional[str] = None, metrics_file: Optional[str] = None, enable_system_metrics: bool = True):
    """Setup OpenTelemetry metrics with system resource monitoring and local JSON file export.

    This function configures OpenTelemetry Metrics to collect:
    - CPU usage (percentage)
    - Memory usage (percentage and absolute values)
    - Process-specific metrics

    Metrics are exported to local JSON files, similar to trace export.

    Note: In containerized environments, psutil will report container-level metrics,
    not host-level metrics. For host-level metrics, use cadvisor.

    Args:
        service_name: Service name for resource attributes
        metrics_file: Metrics file path, defaults to OTEL_METRICS_FILE env var or auto-generated
        enable_system_metrics: Whether to enable CPU/memory monitoring (default: True)
    """
    global _metrics_initialized, _metrics_thread

    if _metrics_initialized:
        logger.warning("Metrics already initialized, skipping")
        return

    try:
        service_name = service_name or os.getenv("OTEL_SERVICE_NAME", "adk-agent")
        metrics_file = metrics_file or os.getenv("OTEL_METRICS_FILE")

        if not metrics_file:
            # Default file path: metrics directory in current working directory
            metrics_dir = Path.cwd() / "metrics"
            metrics_dir.mkdir(exist_ok=True)
            # Use unified timestamp from environment variable if available (for distributed systems)
            # Otherwise, generate timestamp locally (for standalone agents)
            timestamp = os.getenv("OTEL_RUN_TIMESTAMP")
            if not timestamp:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            metrics_file = str(metrics_dir / f"{service_name}_{timestamp}.jsonl")

        logger.info(f"Setting up OpenTelemetry metrics for {service_name}")
        logger.info(f"Metrics file: {metrics_file}")

        # Create resource
        resource = Resource.create({
            "service.name": service_name,
            "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
            "deployment.environment": os.getenv("OTEL_DEPLOYMENT_ENVIRONMENT", "local"),
        })

        # Create JSON file exporter
        json_exporter = JsonFileMetricExporter(metrics_file)

        # Create metric reader with periodic export (every 0.1 seconds for fine granularity)
        reader = PeriodicExportingMetricReader(
            exporter=json_exporter,
            export_interval_millis=100,  # Export every 0.1 seconds for fine granularity
        )

        # Create meter provider
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        metrics.set_meter_provider(meter_provider)

        meter = metrics.get_meter(__name__)

        if enable_system_metrics:
            try:
                import psutil

                # Create observable gauges for CPU and memory
                cpu_gauge = meter.create_observable_gauge(
                    name="system.cpu.usage",
                    description="CPU usage percentage",
                    unit="%",
                    callbacks=[_get_cpu_usage],
                )

                memory_gauge = meter.create_observable_gauge(
                    name="system.memory.usage",
                    description="Memory usage percentage",
                    unit="%",
                    callbacks=[_get_memory_usage],
                )

                memory_bytes_gauge = meter.create_observable_gauge(
                    name="system.memory.usage_bytes",
                    description="Memory usage in bytes",
                    unit="bytes",
                    callbacks=[_get_memory_usage_bytes],
                )

                # Process-specific metrics
                process_cpu_gauge = meter.create_observable_gauge(
                    name="process.cpu.usage",
                    description="Process CPU usage percentage",
                    unit="%",
                    callbacks=[_get_process_cpu_usage],
                )

                process_memory_gauge = meter.create_observable_gauge(
                    name="process.memory.usage_bytes",
                    description="Process memory usage in bytes",
                    unit="bytes",
                    callbacks=[_get_process_memory_usage],
                )

                logger.info("System metrics (CPU/memory) enabled using psutil")
                logger.info("Note: In containers, psutil reports container-level metrics")

            except ImportError:
                logger.warning("psutil not installed, skipping system metrics. Install with: pip install psutil")
            except Exception as e:
                logger.warning(f"Failed to setup system metrics: {e}")

        _metrics_initialized = True
        logger.info("Meter provider configured with JSON file exporter")
        logger.info("OpenTelemetry metrics setup completed")

    except Exception as e:
        logger.error(f"Failed to setup metrics: {e}", exc_info=True)
        # Don't raise exception, allow application to continue (metrics are optional)


def _get_cpu_usage(callback_options):
    """Callback to get system CPU usage."""
    try:
        import psutil
        from opentelemetry.metrics import Observation
        cpu_percent = psutil.cpu_percent(interval=None)
        return [Observation(cpu_percent)]
    except Exception:
        return []


def _get_memory_usage(callback_options):
    """Callback to get system memory usage percentage."""
    try:
        import psutil
        from opentelemetry.metrics import Observation
        memory = psutil.virtual_memory()
        return [Observation(memory.percent)]
    except Exception:
        return []


def _get_memory_usage_bytes(callback_options):
    """Callback to get system memory usage in bytes."""
    try:
        import psutil
        from opentelemetry.metrics import Observation
        memory = psutil.virtual_memory()
        return [Observation(memory.used)]
    except Exception:
        return []


# Global process object for CPU monitoring
_process_obj = None

def _get_process_cpu_usage(callback_options):
    """Callback to get process CPU usage percentage.

    Note: psutil.Process().cpu_percent(interval=None) requires two calls to calculate.
    First call returns 0.0, subsequent calls return the percentage since last call.
    We use interval=0.1 to get immediate accurate reading.
    """
    global _process_obj
    try:
        import psutil
        from opentelemetry.metrics import Observation

        if _process_obj is None:
            _process_obj = psutil.Process()
            # First call to initialize, returns 0.0
            _process_obj.cpu_percent(interval=0.5)
            return [Observation(0.0)]

        # Subsequent calls return actual CPU usage
        # Use interval=0.1 for more accurate reading
        cpu_percent = _process_obj.cpu_percent(interval=0.5)
        return [Observation(cpu_percent)]
    except Exception as e:
        # If error, return 0.0 instead of empty list
        return [Observation(0.0)]


def _get_process_memory_usage(callback_options):
    """Callback to get process memory usage in bytes."""
    global _process_obj
    try:
        import psutil
        from opentelemetry.metrics import Observation

        if _process_obj is None:
            _process_obj = psutil.Process()

        memory_info = _process_obj.memory_info()
        return [Observation(memory_info.rss)]  # Resident Set Size
    except Exception:
        return []
