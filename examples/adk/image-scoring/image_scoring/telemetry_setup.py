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
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, SimpleSpanProcessor, BatchSpanProcessor
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
                                    point_attrs = dict(data_point.attributes) if hasattr(data_point, 'attributes') and data_point.attributes else {}
                                    
                                    # Add service name as agent identifier if not present
                                    if "agent.name" not in point_attrs:
                                        # Try to infer from resource attributes or use service name
                                        agent_name = resource_attrs.get("service.name", "image-scoring")
                                        point_attrs["agent.name"] = agent_name
                                    
                                    point_dict = {
                                        "value": data_point.value if hasattr(data_point, 'value') else None,
                                        "timestamp": data_point.time_unix_nano if hasattr(data_point, 'time_unix_nano') else None,
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
            logger.error(f"Failed to export metrics to {self.file_path}: {e}", exc_info=True)
            return MetricExportResult.FAILURE
    
    def force_flush(self, timeout_millis: int = 30000, **kwargs):
        """Force flush any pending metrics.
        
        Args:
            timeout_millis: Maximum time to wait for flush.
            **kwargs: Additional arguments.
        """
        # For file exporter, data is written immediately, so no explicit flush needed
        pass
    
    def shutdown(self, timeout_millis: int = 30000, **kwargs):
        """Shutdown the exporter.
        
        Args:
            timeout_millis: Maximum time to wait for shutdown.
            **kwargs: Additional arguments.
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
            if not spans:
                logger.warning(f"export() called with empty spans list for {self.file_path}")
                return SpanExportResult.SUCCESS
            
            # Debug logging: log all spans received
            logger.info(f"[DEBUG] export() called with {len(spans)} spans for {self.file_path}")
            for i, span in enumerate(spans):
                logger.info(f"[DEBUG] Span {i}: name={span.name}, kind={getattr(span, 'kind', 'N/A')}, type={type(span).__name__}")
            
            logger.info(f"export() called with {len(spans)} spans for {self.file_path}")
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
                    if "call_llm" in span.name:
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
                    attributes["gen_ai.llm.call.count"] = 1 if "call_llm" in span.name else 0
                if "gen_ai.mcp.call.count" not in attributes:
                    attributes["gen_ai.mcp.call.count"] = 0
                
                # Ensure gen_ai.operation.name is present (required by template)
                if "gen_ai.operation.name" not in attributes:
                    if "call_llm" in span.name:
                        attributes["gen_ai.operation.name"] = "call_llm"
                    elif "invoke_agent" in span.name:
                        attributes["gen_ai.operation.name"] = "invoke_agent"
                    elif "execute_tool" in span.name:
                        attributes["gen_ai.operation.name"] = "execute_tool"
                    elif span.name.lower() == "invocation":
                        # invocation spans are top-level entry points
                        attributes["gen_ai.operation.name"] = "invocation"
                    elif "invocation" in span.name.lower():
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
                
                # Ensure gen_ai.agent.name is present for all spans (required by template)
                # For call_llm and execute_tool spans, try to get from parent context or use extracted agent_name
                if "gen_ai.agent.name" not in attributes:
                    # First try to use the extracted agent_name (even if "unknown")
                    # For call_llm spans, we can try to extract from gcp.vertex.agent labels
                    if "call_llm" in span.name or "execute_tool" in span.name:
                        # Try to get agent name from gcp.vertex.agent labels in llm_request
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
                        
                        # If still not found and agent_name is not "unknown", use it
                        if "gen_ai.agent.name" not in attributes and agent_name != "unknown":
                            attributes["gen_ai.agent.name"] = agent_name
                        # If still not found, try to get from agent.name
                        elif "gen_ai.agent.name" not in attributes and "agent.name" in attributes:
                            attributes["gen_ai.agent.name"] = attributes["agent.name"]
                    else:
                        # For other span types, use the extracted agent_name
                        if agent_name != "unknown":
                            attributes["gen_ai.agent.name"] = agent_name
                        elif "agent.name" in attributes:
                            attributes["gen_ai.agent.name"] = attributes["agent.name"]
                
                # Note: CPU/memory metrics are collected separately in metrics JSONL file,
                # so we don't add them to span attributes (per template guidelines)
                
                # Calculate communication metrics for agent-to-agent calls
                communication_metrics = {}
                input_size = 0
                output_size = 0
                
                # Check if this is agent-to-agent communication
                # Only AgentTool calls are agent communication, not FunctionTool calls
                is_agent_comm = False
                if "execute_tool" in span.name:
                    # Check tool type: only AgentTool is agent communication
                    tool_type = attributes.get("gen_ai.tool.type", "")
                    if tool_type == "AgentTool":
                        is_agent_comm = True
                    # Also check for send_message tool in distributed systems
                    elif tool_type != "FunctionTool":  # If not explicitly FunctionTool, check tool name
                        tool_name = attributes.get("gen_ai.tool.name", "").lower()
                        if "message" in tool_name or "agent" in tool_name:
                            is_agent_comm = True
                elif "invoke_agent" in span.name:
                    # For invoke_agent spans, we cannot determine if it's agent-to-agent communication here
                    # because we don't have access to the parent span in the export method.
                    # The actual parent check will be done in extractors.py based on the exported trace data.
                    # However, we should still try to calculate message size for all invoke_agent spans,
                    # and extractors.py will filter based on parent span information.
                    # This is similar to AgentTool in marketing-agency, but uses invoke_agent spans instead of execute_tool spans
                    is_agent_comm = False  # Will be determined in extractors.py based on parent span
                    
                    # For invoke_agent spans (SequentialAgent/LoopAgent): measure actual input/output data
                    # Try to get tool_call_args and tool_response (same attributes as execute_tool)
                    tool_call_args = attributes.get("gcp.vertex.agent.tool_call_args", "")
                    if tool_call_args and isinstance(tool_call_args, str) and tool_call_args != "{}":
                        input_size = len(tool_call_args.encode('utf-8'))
                    
                    tool_response = attributes.get("gcp.vertex.agent.tool_response", "")
                    if tool_response and isinstance(tool_response, str) and tool_response != "{}":
                        output_size = len(tool_response.encode('utf-8'))
                    
                    # If not found in gcp.vertex.agent.*, try to serialize Python objects from events
                    # Check span events for input/output data
                    if input_size == 0 and output_size == 0 and hasattr(span, 'events') and span.events:
                        try:
                            for event in span.events:
                                event_attrs = dict(event.attributes) if event.attributes else {}
                                # Look for input/output data in events
                                if 'input' in event.name.lower() or 'request' in event.name.lower():
                                    event_data = event_attrs.get('data') or event_attrs.get('input') or event_attrs.get('request')
                                    if event_data:
                                        if isinstance(event_data, str):
                                            input_size = len(event_data.encode('utf-8'))
                                        else:
                                            input_size = len(json.dumps(event_data).encode('utf-8'))
                                elif 'output' in event.name.lower() or 'response' in event.name.lower():
                                    event_data = event_attrs.get('data') or event_attrs.get('output') or event_attrs.get('response')
                                    if event_data:
                                        if isinstance(event_data, str):
                                            output_size = len(event_data.encode('utf-8'))
                                        else:
                                            output_size = len(json.dumps(event_data).encode('utf-8'))
                        except:
                            pass
                
                if is_agent_comm:
                    communication_metrics["is_agent_communication"] = True
                    
                    # For execute_tool spans (AgentTool): measure actual tool call arguments and responses
                    if "execute_tool" in span.name:
                        # Input: tool call arguments (actual data passed to the agent)
                        tool_call_args = attributes.get("gcp.vertex.agent.tool_call_args", "")
                        if tool_call_args and isinstance(tool_call_args, str) and tool_call_args != "{}":
                            input_size = len(tool_call_args.encode('utf-8'))
                        
                        # Output: tool response (actual data returned from the agent)
                        tool_response = attributes.get("gcp.vertex.agent.tool_response", "")
                        if tool_response and isinstance(tool_response, str) and tool_response != "{}":
                            output_size = len(tool_response.encode('utf-8'))
                
                # For call_llm spans: measure actual LLM request and response
                elif "call_llm" in span.name:
                    # Input: LLM request (actual data sent to LLM)
                    llm_request = attributes.get("gcp.vertex.agent.llm_request", "")
                    if llm_request and isinstance(llm_request, str) and llm_request != "{}":
                        input_size = len(llm_request.encode('utf-8'))
                    
                    # Output: LLM response (actual data received from LLM)
                    llm_response = attributes.get("gcp.vertex.agent.llm_response", "")
                    if llm_response and isinstance(llm_response, str) and llm_response != "{}":
                        output_size = len(llm_response.encode('utf-8'))
                
                # Store communication metrics and add to attributes for template compliance
                # Template shows these fields, but per README: "If a field doesn't apply, omit it"
                # So we only add them when there's actual communication data
                # Ensure is_in_process_call is always present (default false)
                if "is_in_process_call" not in communication_metrics:
                    communication_metrics["is_in_process_call"] = False
                
                if input_size > 0 or output_size > 0:
                    if input_size > 0:
                        communication_metrics["input_message_size_bytes"] = input_size
                        attributes["communication.input_message_size_bytes"] = input_size
                    if output_size > 0:
                        communication_metrics["output_message_size_bytes"] = output_size
                        attributes["communication.output_message_size_bytes"] = output_size
                    communication_metrics["total_message_size_bytes"] = input_size + output_size
                    attributes["communication.total_message_size_bytes"] = input_size + output_size
                elif "invoke_agent" in span.name:
                    # For invoke_agent spans, even if we don't have message size data,
                    # we should still mark that we tried to calculate it
                    # This helps extractors.py identify these spans as potential agent-to-agent calls
                    # For in-process calls, message size is 0 (Python objects, not serialized)
                    communication_metrics["is_in_process_call"] = True
                    communication_metrics["input_message_size_bytes"] = 0
                    communication_metrics["output_message_size_bytes"] = 0
                    communication_metrics["total_message_size_bytes"] = 0
                
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
            if span_data:
                with open(self.file_path, "a", encoding="utf-8") as f:
                    for span_dict in span_data:
                        f.write(json.dumps(span_dict, default=str) + "\n")
                logger.info(f"Exported {len(span_data)} spans to {self.file_path}")
                
                # Debug: Log span types summary
                span_types = {}
                for span_dict in span_data:
                    span_name = span_dict.get("name", "unknown")
                    span_types[span_name] = span_types.get(span_name, 0) + 1
                logger.info(f"[DEBUG] Span types summary: {span_types}")
            else:
                logger.warning(f"No spans to export (received {len(spans)} spans, but span_data is empty)")
            
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
            # Use unified timestamp if available, otherwise generate new one
            timestamp = os.getenv("OTEL_RUN_TIMESTAMP") or datetime.now().strftime("%Y%m%d_%H%M%S")
            trace_file = str(trace_dir / f"{service_name}_{timestamp}.jsonl")
        
        logger.info(f"Setting up OpenTelemetry tracing for {service_name}")
        logger.info(f"Trace file: {trace_file}")
        
        # Debug: Log current tracer provider state
        current_provider = trace.get_tracer_provider()
        logger.info(f"[DEBUG] Current tracer provider: {type(current_provider).__name__}")
        
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
            # Use existing provider
            # Note: TracerProvider.resource is read-only, so we can't update it
            # If resource attributes need to be merged, we should create a new provider
            # For now, we'll just use the existing provider as-is
            tracer_provider = current_provider
            logger.debug(f"Using existing TracerProvider (resource attributes cannot be updated)")
        
        # Create and add JSON file exporter
        json_exporter = JsonFileSpanExporter(trace_file)
        # Use SimpleSpanProcessor to export spans immediately when they end
        # This ensures spans are written to file even if process exits abruptly
        span_processor = SimpleSpanProcessor(json_exporter)
        tracer_provider.add_span_processor(span_processor)
        logger.info(f"Using SimpleSpanProcessor for trace export to {trace_file}")
        
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
        logger.info(f"[DEBUG] OTLP endpoint: {otel_endpoint or 'Not set (using JSON file exporter only)'}")
        
        if otel_endpoint:
            try:
                from google.adk.telemetry.setup import maybe_set_otel_providers
                logger.info("[DEBUG] Calling maybe_set_otel_providers()...")
                maybe_set_otel_providers()
                logger.info(f"ADK OTLP exporter enabled for {otel_endpoint}")
            except Exception as e:
                logger.warning(f"Failed to enable ADK OTLP exporter: {e}")
        else:
            logger.info("[DEBUG] OTLP endpoint not set, ADK telemetry will use our JSON file exporter")
        
        # Debug: Log final tracer provider state
        final_provider = trace.get_tracer_provider()
        logger.info(f"[DEBUG] Final tracer provider: {type(final_provider).__name__}")
        if hasattr(final_provider, 'resource') and final_provider.resource:
            logger.info(f"[DEBUG] Tracer provider resource: {dict(final_provider.resource.attributes)}")
        
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
            # Use unified timestamp if available, otherwise generate new one
            timestamp = os.getenv("OTEL_RUN_TIMESTAMP") or datetime.now().strftime("%Y%m%d_%H%M%S")
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
        
        # Create metric reader with periodic export (every 1 seconds)
        reader = PeriodicExportingMetricReader(
            exporter=json_exporter,
            export_interval_millis=1000,  # Export every 1 seconds
        )
        
        # Create meter provider
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        metrics.set_meter_provider(meter_provider)
        
        meter = metrics.get_meter(__name__)
        
        # Create custom metrics for agent communication
        # These will be updated via span processor hooks
        communication_counter = meter.create_counter(
            name="agent.communication.count",
            description="Number of agent-to-agent communications",
            unit="1",
        )
        
        communication_duration = meter.create_histogram(
            name="agent.communication.duration",
            description="Duration of agent-to-agent communications",
            unit="ms",
        )
        
        communication_message_size = meter.create_histogram(
            name="agent.communication.message_size",
            description="Size of messages passed between agents",
            unit="bytes",
        )
        
        logger.info("Custom communication metrics created")
        
        if enable_system_metrics:
            try:
                import psutil
                
                # Process-specific metrics only (system metrics removed per user request)
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
                
                logger.info("Process metrics (CPU/memory) enabled using psutil")
                logger.info("Note: System-level metrics removed per user request")
                
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
    # Removed - system CPU not needed per user request
    return []


def _get_memory_usage(callback_options):
    """Callback to get system memory usage percentage."""
    # Removed - system memory not needed per user request
    return []


def _get_memory_usage_bytes(callback_options):
    """Callback to get system memory usage in bytes."""
    # Removed - system memory not needed per user request
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
    try:
        import psutil
        from opentelemetry.metrics import Observation
        process = psutil.Process()
        memory_info = process.memory_info()
        return [Observation(memory_info.rss)]  # Resident Set Size
    except Exception:
        return []

