import base64
import gzip
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Try to import protobuf dependencies
try:
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
    from opentelemetry.proto.common.v1 import common_pb2
    from opentelemetry.proto.logs.v1 import logs_pb2
    from opentelemetry.proto.metrics.v1 import metrics_pb2
    from opentelemetry.proto.resource.v1 import resource_pb2
    from opentelemetry.proto.trace.v1 import trace_pb2

    PROTOBUF_AVAILABLE = True
except ImportError as e:
    PROTOBUF_AVAILABLE = False
    logging.warning(f"Protobuf libraries not available: {e}")

logger = logging.getLogger(__name__)


class DataHandler:
    """Handle data storage, retrieval, and processing for the fake OTEL collector"""

    def __init__(self, storage_dir: str = "storage", max_memory_items: int = 1000):
        self.storage_dir = storage_dir
        self.max_memory_items = max_memory_items
        self.data_lock = threading.Lock()

        # In-memory storage
        self.collected_data = {"traces": [], "metrics": [], "logs": []}

        # Statistics
        self.stats = {
            "traces_received": 0,
            "metrics_received": 0,
            "logs_received": 0,
            "errors": 0,
            "start_time": datetime.now(),
        }

        # Ensure storage directory exists
        os.makedirs(storage_dir, exist_ok=True)

        # Create subdirectories for each data type
        for data_type in ["traces", "metrics", "logs"]:
            subdir = os.path.join(storage_dir, data_type)
            os.makedirs(subdir, exist_ok=True)

    def process_data(
        self, data_type: str, raw_data: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """Process incoming OTEL data"""
        try:
            content_type = headers.get("Content-Type", "")
            content_encoding = headers.get("Content-Encoding", "")

            processed_data = {
                "timestamp": datetime.now().isoformat(),
                "content_type": content_type,
                "content_encoding": content_encoding,
                "headers": headers,
                "data_size": len(raw_data),
                "processed_size": 0,
                "data_type": data_type,
            }

            # Handle compression
            decompressed_data = self._decompress_data(raw_data, content_encoding)
            processed_data["processed_size"] = len(decompressed_data)

            # Parse data based on content type
            if "application/json" in content_type:
                processed_data.update(self._parse_json_data(decompressed_data))
            elif (
                "application/x-protobuf" in content_type
                or "application/octet-stream" in content_type
            ):
                processed_data.update(
                    self._parse_protobuf_data(decompressed_data, data_type)
                )
            else:
                processed_data.update(self._parse_unknown_data(decompressed_data))

            # Store the processed data (automatically saves to organized folders)
            self._store_data(data_type, processed_data)

            # Update statistics
            self._update_stats(data_type)

            logger.info(
                f"Processed {data_type} data: {processed_data['data_size']} bytes"
            )

            return processed_data

        except Exception as e:
            logger.error(f"Error processing {data_type} data: {e}")
            self.stats["errors"] += 1
            raise

    def _decompress_data(self, data: bytes, encoding: str) -> bytes:
        """Decompress data if needed"""
        if encoding == "gzip":
            try:
                return gzip.decompress(data)
            except Exception as e:
                logger.warning(f"Failed to decompress gzip data: {e}")
                return data
        elif encoding == "deflate":
            try:
                import zlib

                return zlib.decompress(data)
            except Exception as e:
                logger.warning(f"Failed to decompress deflate data: {e}")
                return data
        return data

    def _parse_json_data(self, data: bytes) -> Dict[str, Any]:
        """Parse JSON data"""
        try:
            json_data = json.loads(data.decode("utf-8"))
            return {"format": "json", "data": json_data, "parsed_successfully": True}
        except Exception as e:
            return {
                "format": "json_error",
                "error": str(e),
                "raw_data": data.decode("utf-8", errors="ignore")[:500],
                "parsed_successfully": False,
            }

    def _parse_protobuf_data(self, data: bytes, data_type: str) -> Dict[str, Any]:
        """Parse protobuf data with real OTLP parsing"""
        if not PROTOBUF_AVAILABLE:
            return self._parse_protobuf_mock(data)

        try:
            # Parse based on expected data type
            if data_type == "metrics":
                try:
                    metrics_request = metrics_service_pb2.ExportMetricsServiceRequest()
                    metrics_request.ParseFromString(data)
                    parsed_data = self._parse_metrics_protobuf(metrics_request)
                    return {
                        "format": "protobuf",
                        "data": parsed_data,
                        "processed_size": len(data),
                        "data_type_detected": "metrics",
                        "parsed_successfully": True,
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse as metrics: {e}")

            elif data_type == "traces":
                try:
                    trace_request = trace_service_pb2.ExportTraceServiceRequest()
                    trace_request.ParseFromString(data)
                    parsed_data = self._parse_traces_protobuf(trace_request)
                    return {
                        "format": "protobuf",
                        "data": parsed_data,
                        "processed_size": len(data),
                        "data_type_detected": "traces",
                        "parsed_successfully": True,
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse as traces: {e}")

            elif data_type == "logs":
                try:
                    logs_request = logs_service_pb2.ExportLogsServiceRequest()
                    logs_request.ParseFromString(data)
                    parsed_data = self._parse_logs_protobuf(logs_request)
                    return {
                        "format": "protobuf",
                        "data": parsed_data,
                        "processed_size": len(data),
                        "data_type_detected": "logs",
                        "parsed_successfully": True,
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse as logs: {e}")

            # Fall back to mock parsing if specific type parsing fails
            return self._parse_protobuf_mock(data)

        except Exception as e:
            logger.warning(f"Protobuf parsing failed: {e}")
            return self._parse_protobuf_mock(data)

    def _parse_protobuf_mock(self, data: bytes) -> Dict[str, Any]:
        """Mock protobuf parsing when real parsing fails"""
        try:
            data_preview = data[:100] if len(data) > 100 else data
            mock_structure = {
                "resource_spans": [] if "span" in str(data_preview).lower() else None,
                "resource_metrics": []
                if "metric" in str(data_preview).lower()
                else None,
                "resource_logs": [] if "log" in str(data_preview).lower() else None,
                "extracted_fields": self._extract_protobuf_fields(data),
            }

            return {
                "format": "protobuf_mock",
                "data": mock_structure,
                "raw_size": len(data),
                "preview": base64.b64encode(data_preview).decode("utf-8"),
                "parsed_successfully": False,
                "note": "Mock parsing - install opentelemetry-proto for full parsing",
            }

        except Exception as e:
            return {
                "format": "protobuf_error",
                "error": str(e),
                "raw_size": len(data),
                "preview": base64.b64encode(data[:100]).decode("utf-8") if data else "",
                "parsed_successfully": False,
            }

    def _parse_attributes(self, attributes_pb):
        """Parse protobuf attributes to dict"""
        if not PROTOBUF_AVAILABLE:
            return {}

        result = {}
        for attr in attributes_pb:
            key = attr.key
            value = attr.value

            if value.HasField("string_value"):
                result[key] = value.string_value
            elif value.HasField("int_value"):
                result[key] = value.int_value
            elif value.HasField("double_value"):
                result[key] = value.double_value
            elif value.HasField("bool_value"):
                result[key] = value.bool_value
            elif value.HasField("bytes_value"):
                result[key] = base64.b64encode(value.bytes_value).decode("utf-8")
            elif value.HasField("array_value"):
                result[key] = [
                    self._parse_any_value(v) for v in value.array_value.values
                ]
            elif value.HasField("kvlist_value"):
                result[key] = self._parse_attributes(value.kvlist_value.values)
            else:
                result[key] = str(value)

        return result

    def _parse_any_value(self, value):
        """Parse AnyValue protobuf field"""
        if not PROTOBUF_AVAILABLE:
            return str(value)

        if value.HasField("string_value"):
            return value.string_value
        elif value.HasField("int_value"):
            return value.int_value
        elif value.HasField("double_value"):
            return value.double_value
        elif value.HasField("bool_value"):
            return value.bool_value
        elif value.HasField("bytes_value"):
            return base64.b64encode(value.bytes_value).decode("utf-8")
        elif value.HasField("array_value"):
            return [self._parse_any_value(v) for v in value.array_value.values]
        elif value.HasField("kvlist_value"):
            return self._parse_attributes(value.kvlist_value.values)
        else:
            return str(value)

    def _parse_resource(self, resource):
        """Parse resource protobuf field"""
        if not PROTOBUF_AVAILABLE:
            return {}

        return {
            "attributes": self._parse_attributes(resource.attributes),
            "dropped_attributes_count": resource.dropped_attributes_count,
        }

    def _parse_instrumentation_scope(self, scope):
        """Parse instrumentation scope protobuf field"""
        if not PROTOBUF_AVAILABLE:
            return {}

        return {
            "name": scope.name,
            "version": scope.version,
            "attributes": self._parse_attributes(scope.attributes),
            "dropped_attributes_count": scope.dropped_attributes_count,
        }

    def _parse_metrics_protobuf(self, metrics_request):
        """Parse OTLP metrics protobuf data"""
        if not PROTOBUF_AVAILABLE:
            return {"resource_metrics": []}

        parsed_data = {"resource_metrics": []}

        for resource_metric in metrics_request.resource_metrics:
            resource_metric_data = {
                "resource": self._parse_resource(resource_metric.resource)
                if resource_metric.HasField("resource")
                else {},
                "scope_metrics": [],
                "schema_url": resource_metric.schema_url,
            }

            for scope_metric in resource_metric.scope_metrics:
                scope_metric_data = {
                    "scope": self._parse_instrumentation_scope(scope_metric.scope)
                    if scope_metric.HasField("scope")
                    else {},
                    "metrics": [],
                    "schema_url": scope_metric.schema_url,
                }

                for metric in scope_metric.metrics:
                    metric_data = {
                        "name": metric.name,
                        "description": metric.description,
                        "unit": metric.unit,
                        "data": {},
                    }

                    # Parse different metric types
                    if metric.HasField("gauge"):
                        metric_data["data"] = {"type": "gauge", "data_points": []}
                        for dp in metric.gauge.data_points:
                            dp_data = {
                                "attributes": self._parse_attributes(dp.attributes),
                                "time_unix_nano": dp.time_unix_nano,
                                "start_time_unix_nano": dp.start_time_unix_nano
                                if dp.start_time_unix_nano
                                else None,
                            }

                            if dp.HasField("as_double"):
                                dp_data["value"] = dp.as_double
                                dp_data["value_type"] = "double"
                            elif dp.HasField("as_int"):
                                dp_data["value"] = dp.as_int
                                dp_data["value_type"] = "int"

                            metric_data["data"]["data_points"].append(dp_data)

                    elif metric.HasField("sum"):
                        metric_data["data"] = {
                            "type": "sum",
                            "aggregation_temporality": metric.sum.aggregation_temporality,
                            "is_monotonic": metric.sum.is_monotonic,
                            "data_points": [],
                        }
                        for dp in metric.sum.data_points:
                            dp_data = {
                                "attributes": self._parse_attributes(dp.attributes),
                                "time_unix_nano": dp.time_unix_nano,
                                "start_time_unix_nano": dp.start_time_unix_nano,
                            }

                            if dp.HasField("as_double"):
                                dp_data["value"] = dp.as_double
                                dp_data["value_type"] = "double"
                            elif dp.HasField("as_int"):
                                dp_data["value"] = dp.as_int
                                dp_data["value_type"] = "int"

                            metric_data["data"]["data_points"].append(dp_data)

                    elif metric.HasField("histogram"):
                        metric_data["data"] = {
                            "type": "histogram",
                            "aggregation_temporality": metric.histogram.aggregation_temporality,
                            "data_points": [],
                        }
                        for dp in metric.histogram.data_points:
                            dp_data = {
                                "attributes": self._parse_attributes(dp.attributes),
                                "time_unix_nano": dp.time_unix_nano,
                                "start_time_unix_nano": dp.start_time_unix_nano,
                                "count": dp.count,
                                "sum": dp.sum if dp.HasField("sum") else None,
                                "bucket_counts": list(dp.bucket_counts),
                                "explicit_bounds": list(dp.explicit_bounds),
                                "min": dp.min if dp.HasField("min") else None,
                                "max": dp.max if dp.HasField("max") else None,
                            }
                            metric_data["data"]["data_points"].append(dp_data)

                    elif metric.HasField("exponential_histogram"):
                        metric_data["data"] = {
                            "type": "exponential_histogram",
                            "aggregation_temporality": metric.exponential_histogram.aggregation_temporality,
                            "data_points": [],
                        }
                        for dp in metric.exponential_histogram.data_points:
                            dp_data = {
                                "attributes": self._parse_attributes(dp.attributes),
                                "time_unix_nano": dp.time_unix_nano,
                                "start_time_unix_nano": dp.start_time_unix_nano,
                                "count": dp.count,
                                "sum": dp.sum if dp.HasField("sum") else None,
                                "scale": dp.scale,
                                "zero_count": dp.zero_count,
                                "positive": {
                                    "offset": dp.positive.offset,
                                    "bucket_counts": list(dp.positive.bucket_counts),
                                }
                                if dp.HasField("positive")
                                else None,
                                "negative": {
                                    "offset": dp.negative.offset,
                                    "bucket_counts": list(dp.negative.bucket_counts),
                                }
                                if dp.HasField("negative")
                                else None,
                                "min": dp.min if dp.HasField("min") else None,
                                "max": dp.max if dp.HasField("max") else None,
                            }
                            metric_data["data"]["data_points"].append(dp_data)

                    elif metric.HasField("summary"):
                        metric_data["data"] = {"type": "summary", "data_points": []}
                        for dp in metric.summary.data_points:
                            dp_data = {
                                "attributes": self._parse_attributes(dp.attributes),
                                "time_unix_nano": dp.time_unix_nano,
                                "start_time_unix_nano": dp.start_time_unix_nano,
                                "count": dp.count,
                                "sum": dp.sum,
                                "quantile_values": [
                                    {"quantile": qv.quantile, "value": qv.value}
                                    for qv in dp.quantile_values
                                ],
                            }
                            metric_data["data"]["data_points"].append(dp_data)

                    scope_metric_data["metrics"].append(metric_data)

                resource_metric_data["scope_metrics"].append(scope_metric_data)

            parsed_data["resource_metrics"].append(resource_metric_data)

        return parsed_data

    def _parse_traces_protobuf(self, trace_request):
        """Parse OTLP traces protobuf data"""
        if not PROTOBUF_AVAILABLE:
            return {"resource_spans": []}

        parsed_data = {"resource_spans": []}

        for resource_span in trace_request.resource_spans:
            resource_span_data = {
                "resource": self._parse_resource(resource_span.resource)
                if resource_span.HasField("resource")
                else {},
                "scope_spans": [],
                "schema_url": resource_span.schema_url,
            }

            for scope_span in resource_span.scope_spans:
                scope_span_data = {
                    "scope": self._parse_instrumentation_scope(scope_span.scope)
                    if scope_span.HasField("scope")
                    else {},
                    "spans": [],
                    "schema_url": scope_span.schema_url,
                }

                for span in scope_span.spans:
                    span_data = {
                        "trace_id": span.trace_id.hex(),
                        "span_id": span.span_id.hex(),
                        "trace_state": span.trace_state,
                        "parent_span_id": span.parent_span_id.hex()
                        if span.parent_span_id
                        else None,
                        "name": span.name,
                        "kind": span.kind,
                        "start_time_unix_nano": span.start_time_unix_nano,
                        "end_time_unix_nano": span.end_time_unix_nano,
                        "attributes": self._parse_attributes(span.attributes),
                        "dropped_attributes_count": span.dropped_attributes_count,
                        "events": [
                            {
                                "time_unix_nano": event.time_unix_nano,
                                "name": event.name,
                                "attributes": self._parse_attributes(event.attributes),
                                "dropped_attributes_count": event.dropped_attributes_count,
                            }
                            for event in span.events
                        ],
                        "dropped_events_count": span.dropped_events_count,
                        "links": [
                            {
                                "trace_id": link.trace_id.hex(),
                                "span_id": link.span_id.hex(),
                                "trace_state": link.trace_state,
                                "attributes": self._parse_attributes(link.attributes),
                                "dropped_attributes_count": link.dropped_attributes_count,
                            }
                            for link in span.links
                        ],
                        "dropped_links_count": span.dropped_links_count,
                        "status": {
                            "message": span.status.message,
                            "code": span.status.code,
                        },
                    }

                    scope_span_data["spans"].append(span_data)

                resource_span_data["scope_spans"].append(scope_span_data)

            parsed_data["resource_spans"].append(resource_span_data)

        return parsed_data

    def _parse_logs_protobuf(self, logs_request):
        """Parse OTLP logs protobuf data"""
        if not PROTOBUF_AVAILABLE:
            return {"resource_logs": []}

        parsed_data = {"resource_logs": []}

        for resource_log in logs_request.resource_logs:
            resource_log_data = {
                "resource": self._parse_resource(resource_log.resource)
                if resource_log.HasField("resource")
                else {},
                "scope_logs": [],
                "schema_url": resource_log.schema_url,
            }

            for scope_log in resource_log.scope_logs:
                scope_log_data = {
                    "scope": self._parse_instrumentation_scope(scope_log.scope)
                    if scope_log.HasField("scope")
                    else {},
                    "log_records": [],
                    "schema_url": scope_log.schema_url,
                }

                for log_record in scope_log.log_records:
                    log_data = {
                        "time_unix_nano": log_record.time_unix_nano,
                        "observed_time_unix_nano": log_record.observed_time_unix_nano,
                        "severity_number": log_record.severity_number,
                        "severity_text": log_record.severity_text,
                        "body": self._parse_any_value(log_record.body)
                        if log_record.HasField("body")
                        else None,
                        "attributes": self._parse_attributes(log_record.attributes),
                        "dropped_attributes_count": log_record.dropped_attributes_count,
                        "flags": log_record.flags,
                        "trace_id": log_record.trace_id.hex()
                        if log_record.trace_id
                        else None,
                        "span_id": log_record.span_id.hex()
                        if log_record.span_id
                        else None,
                    }

                    scope_log_data["log_records"].append(log_data)

                resource_log_data["scope_logs"].append(scope_log_data)

            parsed_data["resource_logs"].append(resource_log_data)

        return parsed_data

    def _extract_protobuf_fields(self, data: bytes) -> Dict[str, Any]:
        """Extract basic information from protobuf data"""
        fields = {}
        data_str = str(data)

        if "service" in data_str.lower():
            fields["has_service_info"] = True
        if "span" in data_str.lower():
            fields["has_span_data"] = True
        if "metric" in data_str.lower():
            fields["has_metric_data"] = True
        if "log" in data_str.lower():
            fields["has_log_data"] = True

        fields["estimated_records"] = data_str.count("\x12") + data_str.count("\x1a")
        return fields

    def _parse_unknown_data(self, data: bytes) -> Dict[str, Any]:
        """Handle unknown data format"""
        return {
            "format": "unknown",
            "data_size": len(data),
            "preview": data.decode("utf-8", errors="ignore")[:200],
            "hex_preview": data[:50].hex() if data else "",
            "parsed_successfully": False,
        }

    def _store_data(self, data_type: str, processed_data: Dict[str, Any]):
        """Store processed data in memory and optionally to file"""
        with self.data_lock:
            self.collected_data[data_type].append(processed_data)

            # Implement memory limit
            if len(self.collected_data[data_type]) > self.max_memory_items:
                overflow_data = self.collected_data[data_type][:100]
                self._save_to_file(f"{data_type}_overflow", overflow_data, data_type)
                self.collected_data[data_type] = self.collected_data[data_type][100:]

            # Also save individual items to organized folders
            timestamp = datetime.now().isoformat().replace(":", "-")
            filename = f"{data_type}_{timestamp}"
            self._save_single_item_to_folder(data_type, processed_data, filename)

    def _update_stats(self, data_type: str):
        """Update collection statistics"""
        stat_key = f"{data_type}_received"
        if stat_key in self.stats:
            self.stats[stat_key] += 1

    def get_data(
        self, data_type: Optional[str] = None, limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """Retrieve collected data"""
        with self.data_lock:
            if data_type:
                data = self.collected_data.get(data_type, [])
                if limit:
                    data = data[-limit:]
                return {"type": data_type, "count": len(data), "data": data}
            else:
                result = {}
                for dtype in ["traces", "metrics", "logs"]:
                    data = self.collected_data[dtype]
                    if limit:
                        data = data[-limit:]
                    result[dtype] = {"count": len(data), "data": data}
                return result

    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics"""
        with self.data_lock:
            current_time = datetime.now()
            uptime = current_time - self.stats["start_time"]

            return {
                **self.stats,
                "uptime_seconds": uptime.total_seconds(),
                "current_memory_usage": {
                    "traces": len(self.collected_data["traces"]),
                    "metrics": len(self.collected_data["metrics"]),
                    "logs": len(self.collected_data["logs"]),
                    "total": sum(
                        len(self.collected_data[t])
                        for t in ["traces", "metrics", "logs"]
                    ),
                },
            }

    def clear_data(self, data_type: Optional[str] = None):
        """Clear collected data"""
        with self.data_lock:
            if data_type:
                self.collected_data[data_type].clear()
            else:
                for dtype in ["traces", "metrics", "logs"]:
                    self.collected_data[dtype].clear()

    def export_data(self) -> Dict[str, Any]:
        """Export all data to files in organized folders"""
        timestamp = datetime.now().isoformat().replace(":", "-")
        files_created = []

        with self.data_lock:
            for data_type in ["traces", "metrics", "logs"]:
                if self.collected_data[data_type]:
                    filename = f"{data_type}_batch_export_{timestamp}.json"
                    self._save_to_file(
                        filename, self.collected_data[data_type], data_type
                    )
                    files_created.append(
                        os.path.join(self.storage_dir, data_type, filename)
                    )

        return {
            "timestamp": timestamp,
            "files_created": files_created,
            "total_files": len(files_created),
        }

    def _save_to_file(
        self, filename: str, data: List[Dict[str, Any]], data_type: Optional[str] = None
    ):
        """Save data to JSON file in organized folders"""
        if not filename.endswith(".json"):
            filename += ".json"

        # Create path with data type subfolder if specified
        if data_type:
            subdir = os.path.join(self.storage_dir, data_type)
            os.makedirs(subdir, exist_ok=True)
            filepath = os.path.join(subdir, filename)
        else:
            filepath = os.path.join(self.storage_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "exported_at": datetime.now().isoformat(),
                        "count": len(data),
                        "data": data,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            logger.info(f"Saved {len(data)} items to {filepath}")

        except Exception as e:
            logger.error(f"Failed to save data to {filepath}: {e}")
            raise

    def _save_single_item_to_folder(
        self, data_type: str, processed_data: Dict[str, Any], filename: str
    ):
        """Save single processed item to its respective folder"""
        if not filename.endswith(".json"):
            filename += ".json"

        subdir = os.path.join(self.storage_dir, data_type)
        os.makedirs(subdir, exist_ok=True)
        filepath = os.path.join(subdir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(processed_data, f, ensure_ascii=False, indent=2)

            logger.debug(f"Saved single {data_type} item to {filepath}")
        except Exception as e:
            logger.warning(f"Failed to save single item to {filepath}: {e}")

    def cleanup_old_files(self, retention_hours: int = 24):
        """Clean up old export files from all folders"""
        if not os.path.exists(self.storage_dir):
            return []

        cutoff_time = datetime.now() - timedelta(hours=retention_hours)
        cleaned_files = []

        # Clean up files in main storage directory
        for filename in os.listdir(self.storage_dir):
            filepath = os.path.join(self.storage_dir, filename)
            if os.path.isfile(filepath) and filename.endswith(".json"):
                try:
                    file_time = datetime.fromtimestamp(os.path.getctime(filepath))
                    if file_time < cutoff_time:
                        os.remove(filepath)
                        cleaned_files.append(filename)
                        logger.info(f"Cleaned up old file: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to clean up file {filename}: {e}")

        # Clean up files in subdirectories (traces, metrics, logs)
        for data_type in ["traces", "metrics", "logs"]:
            subdir = os.path.join(self.storage_dir, data_type)
            if os.path.exists(subdir):
                for filename in os.listdir(subdir):
                    if filename.endswith(".json"):
                        filepath = os.path.join(subdir, filename)
                        try:
                            file_time = datetime.fromtimestamp(
                                os.path.getctime(filepath)
                            )
                            if file_time < cutoff_time:
                                os.remove(filepath)
                                cleaned_files.append(f"{data_type}/{filename}")
                                logger.info(
                                    f"Cleaned up old file: {data_type}/{filename}"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Failed to clean up file {data_type}/{filename}: {e}"
                            )

        return cleaned_files
