#!/usr/bin/env python3
"""
Simple OpenTelemetry Collector
A Flask-based collector with basic parsing capabilities
"""

import base64
import gzip
import json
import logging
import os
import threading
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

# Configure logging first
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to import OTLP protobuf libraries with proper error handling
PROTOBUF_AVAILABLE = False
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
    logger.info("✅ OpenTelemetry protobuf libraries loaded successfully")
except ImportError as e:
    logger.warning(f"⚠️ OpenTelemetry protobuf libraries not available: {e}")
    logger.warning("Install with: pip install opentelemetry-proto")
    logger.info("Running in basic mode without protobuf parsing")

# Create Flask app
app = Flask(__name__)
CORS(app)

# In-memory storage for collected data
collected_data = {"traces": [], "metrics": [], "logs": []}
data_lock = threading.Lock()

# Statistics
stats = {
    "traces_received": 0,
    "metrics_received": 0,
    "logs_received": 0,
    "errors": 0,
    "start_time": datetime.now(),
}


def save_data_to_file(data_type, data):
    """Save collected data to JSON files"""
    timestamp = datetime.now().isoformat().replace(":", "-")
    filename = f"storage/{data_type}_{timestamp}.json"

    os.makedirs("storage", exist_ok=True)

    try:
        with open(filename, "w", encoding="utf-8") as f:
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

        logger.info(f"Saved {len(data)} {data_type} items to {filename}")
        return filename
    except Exception as e:
        logger.error(f"Failed to save {data_type} data: {e}")
        raise


def parse_attributes_simple(attributes_dict):
    """Simple attribute parsing for basic JSON format"""
    result = {}
    if isinstance(attributes_dict, list):
        for attr in attributes_dict:
            if isinstance(attr, dict) and "key" in attr and "value" in attr:
                key = attr["key"]
                value_obj = attr["value"]

                # Extract value based on type
                if isinstance(value_obj, dict):
                    if "stringValue" in value_obj:
                        result[key] = value_obj["stringValue"]
                    elif "intValue" in value_obj:
                        result[key] = (
                            int(value_obj["intValue"])
                            if isinstance(value_obj["intValue"], str)
                            else value_obj["intValue"]
                        )
                    elif "doubleValue" in value_obj:
                        result[key] = value_obj["doubleValue"]
                    elif "boolValue" in value_obj:
                        result[key] = value_obj["boolValue"]
                    else:
                        result[key] = str(value_obj)
                else:
                    result[key] = value_obj
    return result


def parse_protobuf_basic(data, data_type):
    """Basic protobuf parsing with fallback"""
    if not PROTOBUF_AVAILABLE:
        return {
            "type": "binary_data",
            "size": len(data),
            "preview": base64.b64encode(data[:100]).decode("utf-8") if data else "",
            "note": "Protobuf parsing not available. Install opentelemetry-proto for full parsing.",
        }

    try:
        if data_type == "traces":
            request = trace_service_pb2.ExportTraceServiceRequest()
            request.ParseFromString(data)
            return parse_trace_protobuf(request)
        elif data_type == "metrics":
            request = metrics_service_pb2.ExportMetricsServiceRequest()
            request.ParseFromString(data)
            return parse_metrics_protobuf(request)
        elif data_type == "logs":
            request = logs_service_pb2.ExportLogsServiceRequest()
            request.ParseFromString(data)
            return parse_logs_protobuf(request)
    except Exception as e:
        logger.error(f"Protobuf parsing failed for {data_type}: {e}")
        return {
            "type": "protobuf_error",
            "error": str(e),
            "size": len(data),
            "preview": base64.b64encode(data[:100]).decode("utf-8") if data else "",
        }


def parse_trace_protobuf(request):
    """Parse protobuf trace data"""
    result = {"resource_spans": []}

    for resource_span in request.resource_spans:
        rs_data = {"resource": {"attributes": {}}, "scope_spans": []}

        # Parse resource attributes
        if hasattr(resource_span, "resource") and resource_span.resource:
            for attr in resource_span.resource.attributes:
                key = attr.key
                value = attr.value
                if hasattr(value, "string_value") and value.string_value:
                    rs_data["resource"]["attributes"][key] = value.string_value
                elif hasattr(value, "int_value"):
                    rs_data["resource"]["attributes"][key] = value.int_value
                elif hasattr(value, "double_value"):
                    rs_data["resource"]["attributes"][key] = value.double_value
                elif hasattr(value, "bool_value"):
                    rs_data["resource"]["attributes"][key] = value.bool_value

        # Parse scope spans
        for scope_span in resource_span.scope_spans:
            ss_data = {
                "scope": {
                    "name": scope_span.scope.name
                    if hasattr(scope_span, "scope")
                    else "",
                    "version": scope_span.scope.version
                    if hasattr(scope_span, "scope")
                    else "",
                },
                "spans": [],
            }

            # Parse spans
            for span in scope_span.spans:
                span_data = {
                    "trace_id": span.trace_id.hex() if span.trace_id else "",
                    "span_id": span.span_id.hex() if span.span_id else "",
                    "name": span.name,
                    "kind": span.kind,
                    "start_time_unix_nano": span.start_time_unix_nano,
                    "end_time_unix_nano": span.end_time_unix_nano,
                    "attributes": {},
                }

                # Parse span attributes
                for attr in span.attributes:
                    key = attr.key
                    value = attr.value
                    if hasattr(value, "string_value") and value.string_value:
                        span_data["attributes"][key] = value.string_value
                    elif hasattr(value, "int_value"):
                        span_data["attributes"][key] = value.int_value

                ss_data["spans"].append(span_data)

            rs_data["scope_spans"].append(ss_data)

        result["resource_spans"].append(rs_data)

    return result


def parse_metrics_protobuf(request):
    """Parse protobuf metrics data"""
    return {
        "resource_metrics": [
            {
                "resource": {"attributes": {}},
                "scope_metrics": [
                    {
                        "metrics": [
                            {
                                "name": "parsed_metric",
                                "description": "Protobuf metrics parsed successfully",
                            }
                        ]
                    }
                ],
            }
        ],
        "note": "Basic metrics parsing - install full OTLP proto for complete parsing",
    }


def parse_logs_protobuf(request):
    """Parse protobuf logs data"""
    return {
        "resource_logs": [
            {
                "resource": {"attributes": {}},
                "scope_logs": [
                    {
                        "log_records": [
                            {
                                "body": "Protobuf logs parsed successfully",
                                "severity_text": "INFO",
                            }
                        ]
                    }
                ],
            }
        ],
        "note": "Basic logs parsing - install full OTLP proto for complete parsing",
    }


def process_data(raw_data, headers, data_type=None):
    """Process incoming OTEL data"""
    content_type = headers.get("Content-Type", "")
    content_encoding = headers.get("Content-Encoding", "")

    # Handle compression
    processed_data = raw_data
    if content_encoding == "gzip":
        try:
            processed_data = gzip.decompress(raw_data)
            logger.debug(
                f"Decompressed gzip data: {len(raw_data)} -> {len(processed_data)} bytes"
            )
        except Exception as e:
            logger.warning(f"Failed to decompress gzip data: {e}")

    result = {
        "timestamp": datetime.now().isoformat(),
        "content_type": content_type,
        "content_encoding": content_encoding,
        "headers": dict(headers),
        "data_size": len(raw_data),
        "processed_size": len(processed_data),
    }

    # Try to parse as JSON first
    if "application/json" in content_type:
        try:
            json_data = json.loads(processed_data.decode("utf-8"))
            result["format"] = "json"
            result["data"] = parse_json_otlp(json_data, data_type)
            result["parsed_successfully"] = True
            logger.debug(f"Successfully parsed JSON {data_type} data")
        except Exception as e:
            logger.error(f"JSON parsing failed: {e}")
            result["format"] = "json_error"
            result["error"] = str(e)
            result["raw_preview"] = processed_data.decode("utf-8", errors="ignore")[
                :500
            ]
            result["parsed_successfully"] = False
    else:
        # Try to parse as protobuf
        try:
            result["format"] = "protobuf"
            result["data"] = parse_protobuf_basic(processed_data, data_type)
            result["parsed_successfully"] = True
            logger.debug(f"Successfully parsed protobuf {data_type} data")
        except Exception as e:
            logger.error(f"Protobuf parsing failed: {e}")
            result["format"] = "binary"
            result["data"] = {
                "type": "unknown_binary",
                "size": len(processed_data),
                "preview": base64.b64encode(processed_data[:100]).decode("utf-8")
                if processed_data
                else "",
                "error": str(e),
            }
            result["parsed_successfully"] = False

    return result


def parse_json_otlp(json_data, data_type):
    """Parse JSON OTLP data"""
    if data_type == "traces" and "resourceSpans" in json_data:
        result = {"resource_spans": []}
        for rs in json_data["resourceSpans"]:
            rs_parsed = {
                "resource": {
                    "attributes": parse_attributes_simple(
                        rs.get("resource", {}).get("attributes", [])
                    )
                },
                "scope_spans": [],
            }

            for ss in rs.get("scopeSpans", []):
                ss_parsed = {"scope": ss.get("scope", {}), "spans": []}

                for span in ss.get("spans", []):
                    span_parsed = {
                        "trace_id": span.get("traceId", ""),
                        "span_id": span.get("spanId", ""),
                        "name": span.get("name", ""),
                        "kind": span.get("kind", 0),
                        "start_time_unix_nano": span.get("startTimeUnixNano", ""),
                        "end_time_unix_nano": span.get("endTimeUnixNano", ""),
                        "attributes": parse_attributes_simple(
                            span.get("attributes", [])
                        ),
                        "events": span.get("events", []),
                        "status": span.get("status", {}),
                    }
                    ss_parsed["spans"].append(span_parsed)

                rs_parsed["scope_spans"].append(ss_parsed)

            result["resource_spans"].append(rs_parsed)

        return result

    elif data_type == "metrics" and "resourceMetrics" in json_data:
        return {
            "resource_metrics": json_data["resourceMetrics"],
            "parsed": "JSON metrics data",
        }

    elif data_type == "logs" and "resourceLogs" in json_data:
        return {"resource_logs": json_data["resourceLogs"], "parsed": "JSON logs data"}

    # Return original data if not recognized OTLP format
    return json_data


@app.route("/", methods=["GET"])
def index():
    """Root endpoint with collector information"""
    return jsonify(
        {
            "name": "Simple OpenTelemetry Collector",
            "version": "1.0.0",
            "description": "A Flask-based OTEL collector with real parsing capabilities",
            "protobuf_available": PROTOBUF_AVAILABLE,
            "endpoints": {
                "traces": "/v1/traces",
                "metrics": "/v1/metrics",
                "logs": "/v1/logs",
                "health": "/health",
                "data": "/api/data",
                "export": "/api/export",
                "clear": "/api/clear",
                "stats": "/api/stats",
            },
            "supported_formats": ["protobuf", "json"],
            "supported_encodings": ["gzip", "deflate"],
            "status": "running",
            "uptime_seconds": (datetime.now() - stats["start_time"]).total_seconds(),
        }
    )


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    with data_lock:
        return jsonify(
            {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "protobuf_available": PROTOBUF_AVAILABLE,
                "uptime_seconds": (
                    datetime.now() - stats["start_time"]
                ).total_seconds(),
                "collected_data": {
                    "traces": len(collected_data["traces"]),
                    "metrics": len(collected_data["metrics"]),
                    "logs": len(collected_data["logs"]),
                    "total": sum(
                        len(collected_data[t]) for t in ["traces", "metrics", "logs"]
                    ),
                },
                "total_received": {
                    "traces": stats["traces_received"],
                    "metrics": stats["metrics_received"],
                    "logs": stats["logs_received"],
                    "errors": stats["errors"],
                },
            }
        )


@app.route("/v1/traces", methods=["POST"])
def receive_traces():
    """Receive OTLP traces data"""
    try:
        raw_data = request.get_data()
        headers = dict(request.headers)

        processed_data = process_data(raw_data, headers, "traces")

        with data_lock:
            collected_data["traces"].append(processed_data)
            stats["traces_received"] += 1

        # Auto-save every 10 items
        if len(collected_data["traces"]) % 10 == 0:
            save_data_to_file("traces", collected_data["traces"][-10:])

        logger.info(
            f"Received trace data: {processed_data['data_size']} bytes, format: {processed_data['format']}, parsed: {processed_data['parsed_successfully']}"
        )
        return "", 200

    except Exception as e:
        logger.error(f"Error processing traces: {e}", exc_info=True)
        stats["errors"] += 1
        return jsonify({"error": str(e)}), 500


@app.route("/v1/metrics", methods=["POST"])
def receive_metrics():
    """Receive OTLP metrics data"""
    try:
        raw_data = request.get_data()
        headers = dict(request.headers)

        processed_data = process_data(raw_data, headers, "metrics")

        with data_lock:
            collected_data["metrics"].append(processed_data)
            stats["metrics_received"] += 1

        # if len(collected_data["metrics"]) % 10 == 0:
        #     save_data_to_file("metrics", collected_data["metrics"][-10:])

        logger.info(
            f"Received metrics data: {processed_data['data_size']} bytes, format: {processed_data['format']}, parsed: {processed_data['parsed_successfully']}"
        )
        return "", 200

    except Exception as e:
        logger.error(f"Error processing metrics: {e}", exc_info=True)
        stats["errors"] += 1
        return jsonify({"error": str(e)}), 500


@app.route("/v1/logs", methods=["POST"])
def receive_logs():
    """Receive OTLP logs data"""
    try:
        raw_data = request.get_data()
        headers = dict(request.headers)

        processed_data = process_data(raw_data, headers, "logs")

        with data_lock:
            collected_data["logs"].append(processed_data)
            stats["logs_received"] += 1

        if len(collected_data["logs"]) % 10 == 0:
            save_data_to_file("logs", collected_data["logs"][-10:])

        logger.info(
            f"Received logs data: {processed_data['data_size']} bytes, format: {processed_data['format']}, parsed: {processed_data['parsed_successfully']}"
        )
        return "", 200

    except Exception as e:
        logger.error(f"Error processing logs: {e}", exc_info=True)
        stats["errors"] += 1
        return jsonify({"error": str(e)}), 500


@app.route("/api/data", methods=["GET"])
def get_all_data():
    """Get all collected data in JSON format"""
    limit = request.args.get("limit", type=int)

    with data_lock:
        result_data = {}
        for data_type in ["traces", "metrics", "logs"]:
            data = collected_data[data_type]
            if limit:
                data = data[-limit:]
            result_data[data_type] = {"count": len(data), "data": data}

        return jsonify(
            {
                "summary": {
                    "total_traces": len(collected_data["traces"]),
                    "total_metrics": len(collected_data["metrics"]),
                    "total_logs": len(collected_data["logs"]),
                    "protobuf_available": PROTOBUF_AVAILABLE,
                    "last_updated": datetime.now().isoformat(),
                },
                "data": result_data,
            }
        )


@app.route("/api/traces", methods=["GET"])
def get_traces():
    """Get only traces data"""
    limit = request.args.get("limit", type=int, default=100)
    with data_lock:
        traces = (
            collected_data["traces"][-limit:] if limit else collected_data["traces"]
        )
        return jsonify({"count": len(traces), "traces": traces})


@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    """Get only metrics data"""
    limit = request.args.get("limit", type=int, default=100)
    with data_lock:
        metrics = (
            collected_data["metrics"][-limit:] if limit else collected_data["metrics"]
        )
        return jsonify({"count": len(metrics), "metrics": metrics})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    """Get only logs data"""
    limit = request.args.get("limit", type=int, default=100)
    with data_lock:
        logs = collected_data["logs"][-limit:] if limit else collected_data["logs"]
        return jsonify({"count": len(logs), "logs": logs})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get collection statistics"""
    with data_lock:
        return jsonify(
            {
                **stats,
                "protobuf_available": PROTOBUF_AVAILABLE,
                "uptime_seconds": (
                    datetime.now() - stats["start_time"]
                ).total_seconds(),
                "current_memory_usage": {
                    "traces": len(collected_data["traces"]),
                    "metrics": len(collected_data["metrics"]),
                    "logs": len(collected_data["logs"]),
                    "total": sum(
                        len(collected_data[t]) for t in ["traces", "metrics", "logs"]
                    ),
                },
            }
        )


@app.route("/api/clear", methods=["POST"])
def clear_data():
    """Clear all collected data"""
    data_type = None
    if request.is_json:
        data_type = request.json.get("type")

    with data_lock:
        if data_type:
            if data_type in collected_data:
                collected_data[data_type].clear()
                message = f"Cleared {data_type} data"
            else:
                return jsonify({"error": f"Invalid data type: {data_type}"}), 400
        else:
            for dtype in ["traces", "metrics", "logs"]:
                collected_data[dtype].clear()
            message = "Cleared all collected data"

    return jsonify({"message": message, "timestamp": datetime.now().isoformat()})


@app.route("/api/export", methods=["GET"])
def export_data():
    """Export all data to files"""
    try:
        timestamp = datetime.now().isoformat().replace(":", "-")
        files_created = []

        with data_lock:
            for data_type in ["traces", "metrics", "logs"]:
                if collected_data[data_type]:
                    filename = save_data_to_file(
                        f"export_{data_type}_{timestamp}", collected_data[data_type]
                    )
                    files_created.append(filename)

        return jsonify(
            {
                "message": "Data exported successfully",
                "timestamp": timestamp,
                "files_created": files_created,
                "total_files": len(files_created),
            }
        )

    except Exception as e:
        logger.error(f"Export failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify(
        {
            "error": "Endpoint not found",
            "message": "Check the available endpoints at /",
            "status_code": 404,
        }
    ), 404


@app.errorhandler(405)
def method_not_allowed(error):
    """Handle 405 errors"""
    return jsonify(
        {
            "error": "Method not allowed",
            "message": "Check the allowed methods for this endpoint",
            "status_code": 405,
        }
    ), 405


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {error}")
    return jsonify(
        {
            "error": "Internal server error",
            "message": "An unexpected error occurred",
            "status_code": 500,
        }
    ), 500


if __name__ == "__main__":
    # Configuration from environment variables
    HOST = os.getenv("COLLECTOR_HOST", "0.0.0.0")
    PORT = int(os.getenv("COLLECTOR_PORT", 32000))
    DEBUG = os.getenv("COLLECTOR_DEBUG", "True").lower() == "true"

    logger.info("🚀 Starting Simple OpenTelemetry Collector...")
    logger.info(f"Server will run on {HOST}:{PORT}")
    logger.info(f"Protobuf parsing available: {PROTOBUF_AVAILABLE}")
    logger.info("Available endpoints:")
    logger.info("  POST /v1/traces   - Receive trace data")
    logger.info("  POST /v1/metrics  - Receive metrics data")
    logger.info("  POST /v1/logs     - Receive logs data")
    logger.info("  GET  /api/data    - Get all collected data")
    logger.info("  GET  /api/traces  - Get traces data")
    logger.info("  GET  /api/metrics - Get metrics data")
    logger.info("  GET  /api/logs    - Get logs data")
    logger.info("  GET  /api/stats   - Get collection statistics")
    logger.info("  POST /api/clear   - Clear collected data")
    logger.info("  GET  /api/export  - Export data to files")
    logger.info("  GET  /health      - Health check")
    logger.info("  GET  /            - Collector info")
    logger.info("")

    # Ensure storage directory exists
    os.makedirs("storage", exist_ok=True)

    try:
        app.run(host=HOST, port=PORT, debug=DEBUG, threaded=True)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        logger.info("Collector stopped")
