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

"""AutoGen-compatible OpenTelemetry exporters with local JSON file export support."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence
import re

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

logger = logging.getLogger(__name__)


class AutoGenJsonFileMetricExporter(MetricExporter):
    """Exporter that writes AutoGen metrics to local JSON files."""

    def __init__(self, file_path: str):
        """Initialize JSON file exporter.

        Args:
            file_path: Path to JSON file
        """
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # Set preferred temporality and aggregation
        self._preferred_temporality = None
        self._preferred_aggregation = None

    def export(
        self, metrics_data, timeout_millis: float = 10000, **kwargs
    ) -> MetricExportResult:
        """Export metrics to JSON file.

        Args:
            metrics_data: Metrics data to export
            timeout_millis: Maximum time to wait for export
            **kwargs: Additional arguments

        Returns:
            MetricExportResult.SUCCESS
        """
        try:
            # Convert metrics to serializable format
            metric_records = []

            # Extract metric data from the metrics_data object
            if hasattr(metrics_data, "resource_metrics"):
                for resource_metric in metrics_data.resource_metrics:
                    resource_attrs = (
                        dict(resource_metric.resource.attributes)
                        if resource_metric.resource
                        and resource_metric.resource.attributes
                        else {}
                    )

                    for scope_metric in resource_metric.scope_metrics:
                        scope_name = (
                            scope_metric.scope.name
                            if scope_metric.scope
                            else "autogen-system"
                        )

                        for metric in scope_metric.metrics:
                            metric_name = metric.name
                            metric_description = (
                                metric.description
                                if hasattr(metric, "description")
                                else None
                            )
                            metric_unit = (
                                metric.unit if hasattr(metric, "unit") else None
                            )

                            # Extract data points
                            data_points = []
                            if hasattr(metric, "data") and hasattr(
                                metric.data, "data_points"
                            ):
                                for data_point in metric.data.data_points:
                                    point_attrs = (
                                        dict(data_point.attributes)
                                        if hasattr(data_point, "attributes")
                                        and data_point.attributes
                                        else {}
                                    )

                                    # Add agent identifier if not present
                                    if "agent.name" not in point_attrs:
                                        agent_name = resource_attrs.get(
                                            "service.name", "autogen-multi-agent-system"
                                        )
                                        point_attrs["agent.name"] = agent_name

                                    # Handle different data point types safely
                                    value = None
                                    # Use getattr for safe attribute access
                                    value = getattr(data_point, "value", None)
                                    if value is None:
                                        value = getattr(data_point, "sum", None)
                                    if value is None:
                                        value = getattr(data_point, "count", 0)

                                    point_dict = {
                                        "value": value,
                                        "timestamp": data_point.time_unix_nano
                                        if hasattr(data_point, "time_unix_nano")
                                        else None,
                                        "attributes": point_attrs,
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
            logger.error(
                f"Failed to export metrics to {self.file_path}: {e}", exc_info=True
            )
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 30000, **kwargs) -> bool:
        """Force flush any pending metrics."""
        return True

    def shutdown(self, timeout_millis: float = 30000, **kwargs) -> None:
        """Shutdown the exporter."""
        pass


class AutoGenCompositeMetricExporter(MetricExporter):
    """Composite exporter that sends metrics to both JSON file and SigNoz."""

    def __init__(self, file_path: str, signoz_endpoint: str = "http://localhost:4317"):
        """Initialize composite exporter.

        Args:
            file_path: Path to JSON file
            signoz_endpoint: SigNoz OTLP endpoint URL
        """
        self.json_exporter = AutoGenJsonFileMetricExporter(file_path)
        self.signoz_exporter = OTLPMetricExporter(
            endpoint=signoz_endpoint, insecure=True
        )
        # Set preferred temporality and aggregation
        self._preferred_temporality = None
        self._preferred_aggregation = None

    def export(
        self, metrics_data, timeout_millis: float = 10000, **kwargs
    ) -> MetricExportResult:
        """Export metrics to both JSON file and SigNoz.

        Args:
            metrics_data: Metrics data to export
            timeout_millis: Maximum time to wait for export
            **kwargs: Additional arguments

        Returns:
            MetricExportResult.SUCCESS if at least one export succeeds
        """
        json_result = self.json_exporter.export(metrics_data, timeout_millis, **kwargs)

        try:
            signoz_result = self.signoz_exporter.export(
                metrics_data, timeout_millis, **kwargs
            )
        except Exception as e:
            logger.warning(f"Failed to export metrics to SigNoz: {e}")
            signoz_result = MetricExportResult.FAILURE

        # Return success if at least one export succeeds
        if (
            json_result == MetricExportResult.SUCCESS
            or signoz_result == MetricExportResult.SUCCESS
        ):
            return MetricExportResult.SUCCESS
        return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 30000, **kwargs) -> bool:
        """Force flush any pending metrics."""
        json_result = self.json_exporter.force_flush(timeout_millis, **kwargs)
        try:
            signoz_result = self.signoz_exporter.force_flush(timeout_millis, **kwargs)
            return json_result or bool(signoz_result)
        except Exception as e:
            logger.warning(f"Failed to flush SigNoz exporter: {e}")
            return json_result

    def shutdown(self, timeout_millis: float = 30000, **kwargs) -> None:
        """Shutdown both exporters."""
        self.json_exporter.shutdown(timeout_millis, **kwargs)
        try:
            self.signoz_exporter.shutdown(timeout_millis, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to shutdown SigNoz exporter: {e}")


class AutoGenJsonFileSpanExporter(SpanExporter):
    """Exporter that writes AutoGen spans to local JSON files."""

    def __init__(self, file_path: str):
        """Initialize JSON file exporter.

        Args:
            file_path: Path to JSON file
        """
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
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

                # Extract agent name from span attributes or name
                attributes = dict(span.attributes) if span.attributes else {}
                agent_name = self._extract_agent_name(span, attributes)

                # Add agent_name to attributes if not already present
                if "agent.name" not in attributes and agent_name != "unknown":
                    attributes["agent.name"] = agent_name

                # Calculate communication metrics for AutoGen agent interactions
                communication_metrics = self._calculate_communication_metrics(
                    span, attributes
                )

                span_dict = {
                    "trace_id": format(span.context.trace_id, "032x")
                    if span.context
                    else None,
                    "span_id": format(span.context.span_id, "016x")
                    if span.context
                    else None,
                    "parent_span_id": parent_span_id,
                    "name": span.name,
                    "agent_name": agent_name,
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "duration_ns": span.end_time - span.start_time
                    if span.end_time and span.start_time
                    else None,
                    "status": {
                        "status_code": span.status.status_code.name
                        if span.status
                        else None,
                        "description": span.status.description if span.status else None,
                    },
                    "attributes": attributes,
                    "communication": communication_metrics,
                    "events": [
                        {
                            "name": event.name,
                            "timestamp": event.timestamp,
                            "attributes": dict(event.attributes)
                            if event.attributes
                            else {},
                        }
                        for event in span.events
                    ],
                    "resource": {
                        "attributes": dict(span.resource.attributes)
                        if span.resource and span.resource.attributes
                        else {},
                    },
                }
                span_data.append(span_dict)

            # Append to file (supports incremental writes)
            with open(self.file_path, "a", encoding="utf-8") as f:
                for span_dict in span_data:
                    f.write(json.dumps(span_dict, default=str) + "\n")

            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.error(
                f"Failed to export spans to {self.file_path}: {e}", exc_info=True
            )
            return SpanExportResult.FAILURE

    def _extract_agent_name(self, span: ReadableSpan, attributes: dict) -> str:
        """Extract agent name from span information."""
        # Try multiple sources for agent name
        agent_name = (
            attributes.get("autogen.agent.name")
            or attributes.get("agent.name")
            or attributes.get("gen_ai.agent.name")
            or self._infer_agent_from_span_name(span.name)
            or "unknown"
        )
        return agent_name

    def _infer_agent_from_span_name(self, span_name: str) -> Optional[str]:
        """Infer agent name from span name patterns."""
        # AutoGen specific patterns
        if "AssistantAgent" in span_name:
            return "AssistantAgent"
        elif "UserProxyAgent" in span_name:
            return "UserProxyAgent"
        elif "GroupChat" in span_name:
            return "GroupChat"
        elif "RoundRobinGroupChat" in span_name:
            return "RoundRobinGroupChat"
        # Generic patterns
        elif "_agent" in span_name.lower():
            parts = span_name.lower().split("_agent")
            return parts[0] + "_agent" if parts else None
        elif "agent" in span_name.lower():
            parts = span_name.split()
            for part in parts:
                if "agent" in part.lower():
                    return part
        return None

    def _calculate_communication_metrics(
        self, span: ReadableSpan, attributes: dict
    ) -> dict:
        """Calculate communication metrics for AutoGen interactions."""
        communication_metrics = {}
        input_size = 0
        output_size = 0

        # Check if this is agent communication based on span name and attributes
        is_agent_comm = self._is_agent_communication(span.name, attributes)

        if is_agent_comm:
            communication_metrics["is_agent_communication"] = True

            # Measure message sizes from various AutoGen attributes
            input_size = self._get_message_size(attributes, "input")
            output_size = self._get_message_size(attributes, "output")

            # Store communication metrics only if we have actual data
            if input_size > 0:
                communication_metrics["input_message_size_bytes"] = input_size
            if output_size > 0:
                communication_metrics["output_message_size_bytes"] = output_size
            if input_size > 0 or output_size > 0:
                communication_metrics["total_message_size_bytes"] = (
                    input_size + output_size
                )

        return communication_metrics

    def _is_agent_communication(self, span_name: str, attributes: dict) -> bool:
        """Determine if this span represents agent-to-agent communication."""
        # AutoGen communication patterns
        communication_keywords = [
            "send_message",
            "receive_message",
            "generate_reply",
            "process_message",
            "agent_call",
            "chat",
            "conversation",
            "run_stream",
            "group_chat",
            "round_robin",
        ]

        return any(keyword in span_name.lower() for keyword in communication_keywords)

    def _get_message_size(self, attributes: dict, direction: str) -> int:
        """Extract message size from attributes based on direction (input/output)."""
        size = 0

        # AutoGen specific attributes
        if direction == "input":
            message_attrs = r"gen\_ai\.prompt\.\d+\.content"
            for k in attributes.keys():
                if re.match(message_attrs, k):
                    content = attributes.get(k, "")
                    if content and isinstance(content, str) and content != "{}":
                        size += len(content.encode("utf-8"))
        else:  # output
            message_attrs = r"gen\_ai\.completion\.\d+\.content"
            for k in attributes.keys():
                if re.match(message_attrs, k):
                    content = attributes.get(k, "")
                    if content and isinstance(content, str) and content != "{}":
                        size += len(content.encode("utf-8"))

        return size

    def shutdown(self) -> None:
        """Shutdown the exporter."""
        pass


class AutoGenCompositeSpanExporter(SpanExporter):
    """Composite exporter that sends spans to both JSON file and SigNoz."""

    def __init__(self, file_path: str, signoz_endpoint: str = "http://localhost:4317"):
        """Initialize composite exporter.

        Args:
            file_path: Path to JSON file
            signoz_endpoint: SigNoz OTLP endpoint URL
        """
        self.json_exporter = AutoGenJsonFileSpanExporter(file_path)
        self.signoz_exporter = OTLPSpanExporter(endpoint=signoz_endpoint, insecure=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export spans to both JSON file and SigNoz.

        Args:
            spans: List of spans to export

        Returns:
            SpanExportResult.SUCCESS if at least one export succeeds
        """
        json_result = self.json_exporter.export(spans)

        try:
            signoz_result = self.signoz_exporter.export(spans)
        except Exception as e:
            logger.warning(f"Failed to export spans to SigNoz: {e}")
            signoz_result = SpanExportResult.FAILURE

        # Return success if at least one export succeeds
        if (
            json_result == SpanExportResult.SUCCESS
            or signoz_result == SpanExportResult.SUCCESS
        ):
            return SpanExportResult.SUCCESS
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        """Shutdown both exporters."""
        self.json_exporter.shutdown()
        try:
            self.signoz_exporter.shutdown()
        except Exception as e:
            logger.warning(f"Failed to shutdown SigNoz span exporter: {e}")


def setup_autogen_tracing(
    service_name: Optional[str] = None,
    trace_file: Optional[str] = None,
    enable_signoz: bool = True,
    signoz_endpoint: str = "http://localhost:4317",
):
    """Setup OpenTelemetry tracing for AutoGen with JSON file and SigNoz export.

    Args:
        service_name: Service name, defaults to "autogen-multi-agent-system"
        trace_file: Trace file path, auto-generated if not provided
        enable_signoz: Whether to enable SigNoz export
        signoz_endpoint: SigNoz OTLP endpoint URL

    Returns:
        TracerProvider instance
    """
    try:
        # Get configuration
        service_name = service_name or os.getenv(
            "OTEL_SERVICE_NAME", "autogen-multi-agent-system"
        )
        trace_file = trace_file or os.getenv("OTEL_TRACE_FILE")

        if not trace_file:
            # Default file path: traces directory
            trace_dir = Path.cwd() / "traces"
            trace_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_file = str(trace_dir / f"{service_name}_{timestamp}.jsonl")

        logger.info(f"Setting up AutoGen tracing for {service_name}")
        logger.info(f"Trace file: {trace_file}")

        # Create resource
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
                "deployment.environment": os.getenv(
                    "OTEL_DEPLOYMENT_ENVIRONMENT", "local"
                ),
            }
        )

        # Create tracer provider
        tracer_provider = TracerProvider(resource=resource)

        # Create exporter (composite if SigNoz is enabled)
        if enable_signoz:
            exporter = AutoGenCompositeSpanExporter(trace_file, signoz_endpoint)
            logger.info(f"SigNoz endpoint: {signoz_endpoint}")
        else:
            exporter = AutoGenJsonFileSpanExporter(trace_file)
        span_processor = SimpleSpanProcessor(exporter)
        tracer_provider.add_span_processor(span_processor)

        # Set tracer provider
        trace.set_tracer_provider(tracer_provider)

        # Set environment variables for better instrumentation
        os.environ.setdefault(
            "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED", "true"
        )
        os.environ.setdefault("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", "10000")

        logger.info("AutoGen tracing setup completed")
        return tracer_provider

    except Exception as e:
        logger.error(f"Failed to setup AutoGen tracing: {e}", exc_info=True)
        return None


def setup_autogen_metrics(
    service_name: Optional[str] = None,
    metrics_file: Optional[str] = None,
    enable_system_metrics: bool = True,
    enable_signoz: bool = True,
    signoz_endpoint: str = "http://localhost:4317",
):
    """Setup OpenTelemetry metrics for AutoGen with JSON file and SigNoz export.

    Args:
        service_name: Service name for resource attributes
        metrics_file: Metrics file path, auto-generated if not provided
        enable_system_metrics: Whether to enable CPU/memory monitoring
        enable_signoz: Whether to enable SigNoz export
        signoz_endpoint: SigNoz OTLP endpoint URL

    Returns:
        MeterProvider instance
    """
    try:
        service_name = service_name or os.getenv(
            "OTEL_SERVICE_NAME", "autogen-multi-agent-system"
        )
        metrics_file = metrics_file or os.getenv("OTEL_METRICS_FILE")

        if not metrics_file:
            # Default file path: metrics directory
            metrics_dir = Path.cwd() / "metrics"
            metrics_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            metrics_file = str(metrics_dir / f"{service_name}_{timestamp}.jsonl")

        logger.info(f"Setting up AutoGen metrics for {service_name}")
        logger.info(f"Metrics file: {metrics_file}")

        # Create resource
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": os.getenv("OTEL_SERVICE_VERSION", "1.0.0"),
                "deployment.environment": os.getenv(
                    "OTEL_DEPLOYMENT_ENVIRONMENT", "local"
                ),
            }
        )

        # Create exporter (composite if SigNoz is enabled)
        if enable_signoz:
            exporter = AutoGenCompositeMetricExporter(metrics_file, signoz_endpoint)
            logger.info(f"SigNoz endpoint: {signoz_endpoint}")
        else:
            exporter = AutoGenJsonFileMetricExporter(metrics_file)

        # Create metric reader with periodic export (every 1 second)
        reader = PeriodicExportingMetricReader(
            exporter=exporter,
            export_interval_millis=1000,
        )

        # Create meter provider
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        metrics.set_meter_provider(meter_provider)

        # Create custom metrics for AutoGen
        meter = metrics.get_meter("autogen-metrics")

        # Agent communication metrics
        meter.create_counter(
            name="autogen.agent.communication.count",
            description="Number of agent-to-agent communications",
            unit="1",
        )

        meter.create_histogram(
            name="autogen.agent.communication.duration",
            description="Duration of agent-to-agent communications",
            unit="ms",
        )

        meter.create_histogram(
            name="autogen.agent.message_size",
            description="Size of messages passed between agents",
            unit="bytes",
        )

        # System metrics if enabled
        if enable_system_metrics:
            try:
                import psutil

                def _get_process_cpu_usage(callback_options):
                    """Get process CPU usage percentage."""
                    try:
                        from opentelemetry.metrics import Observation

                        process = psutil.Process()
                        cpu_percent = process.cpu_percent(interval=None)
                        return [Observation(cpu_percent)]
                    except Exception:
                        from opentelemetry.metrics import Observation

                        return [Observation(0.0)]

                def _get_process_memory_usage(callback_options):
                    """Get process memory usage in bytes."""
                    try:
                        from opentelemetry.metrics import Observation

                        process = psutil.Process()
                        memory_info = process.memory_info()
                        return [Observation(memory_info.rss)]
                    except Exception:
                        from opentelemetry.metrics import Observation

                        return [Observation(0)]

                meter.create_observable_gauge(
                    name="autogen.process.cpu.usage",
                    description="Process CPU usage percentage",
                    unit="%",
                    callbacks=[_get_process_cpu_usage],
                )

                meter.create_observable_gauge(
                    name="autogen.process.memory.usage_bytes",
                    description="Process memory usage in bytes",
                    unit="bytes",
                    callbacks=[_get_process_memory_usage],
                )

                logger.info("System metrics enabled")

            except ImportError:
                logger.warning(
                    "psutil not installed, skipping system metrics. Install with: pip install psutil"
                )
            except Exception as e:
                logger.warning(f"Failed to setup system metrics: {e}")

        logger.info("AutoGen metrics setup completed")
        return meter_provider

    except Exception as e:
        logger.error(f"Failed to setup AutoGen metrics: {e}", exc_info=True)
        return None
