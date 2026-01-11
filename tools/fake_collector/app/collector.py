import base64
import gzip
import json
import os
import threading
from datetime import datetime

from flask import Flask, jsonify, request
from google.protobuf.message import DecodeError

app = Flask(__name__)

collected_data = {"traces": [], "metrics": [], "logs": []}

# Lock for thread-safe operations
data_lock = threading.Lock()


def save_data_to_file(data_type, data):
    """Save collected data to JSON files"""
    timestamp = datetime.now().isoformat()
    filename = f"storage/{data_type}_{timestamp.replace(':', '-')}.json"

    os.makedirs("storage", exist_ok=True)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved {data_type} data to {filename}")


def decode_protobuf_data(data):
    """Attempt to decode protobuf data and convert to dict"""
    try:
        # If data is base64 encoded, decode it first
        if isinstance(data, str):
            try:
                data = base64.b64decode(data)
            except:
                pass

        # For this fake collector, we'll just return a mock structure
        # In a real implementation, you'd use the actual OTLP protobuf definitions
        return {
            "decoded": True,
            "timestamp": datetime.now().isoformat(),
            "raw_data_length": len(data) if isinstance(data, bytes) else len(str(data)),
            "content_preview": str(data)[:100] if len(str(data)) > 100 else str(data),
        }
    except Exception as e:
        return {"decoded": False, "error": str(e), "raw_data": str(data)[:100]}


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "collected_traces": len(collected_data["traces"]),
            "collected_metrics": len(collected_data["metrics"]),
            "collected_logs": len(collected_data["logs"]),
        }
    )


@app.route("/v1/traces", methods=["POST"])
def receive_traces():
    """Receive OTLP traces data"""
    try:
        content_type = request.headers.get("Content-Type", "")
        content_encoding = request.headers.get("Content-Encoding", "")

        # Get raw data
        raw_data = request.get_data()

        # Handle gzip compression
        if content_encoding == "gzip":
            try:
                raw_data = gzip.decompress(raw_data)
            except Exception as e:
                print(f"Failed to decompress gzip data: {e}")

        trace_data = {
            "timestamp": datetime.now().isoformat(),
            "content_type": content_type,
            "content_encoding": content_encoding,
            "headers": dict(request.headers),
            "data_size": len(raw_data),
        }

        # Try to decode as JSON first
        if "application/json" in content_type:
            try:
                json_data = json.loads(raw_data.decode("utf-8"))
                trace_data["format"] = "json"
                trace_data["data"] = json_data
            except Exception as e:
                trace_data["format"] = "json_error"
                trace_data["error"] = str(e)
                trace_data["raw_data"] = raw_data.decode("utf-8", errors="ignore")[:500]
        else:
            # Assume protobuf format
            trace_data["format"] = "protobuf"
            trace_data["data"] = decode_protobuf_data(raw_data)

        with data_lock:
            collected_data["traces"].append(trace_data)

        # Save to file periodically
        if len(collected_data["traces"]) % 10 == 0:
            save_data_to_file("traces", collected_data["traces"])

        print(
            f"Received trace data: {trace_data['data_size']} bytes, format: {trace_data['format']}"
        )

        return "", 200

    except Exception as e:
        print(f"Error processing traces: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/v1/metrics", methods=["POST"])
def receive_metrics():
    """Receive OTLP metrics data"""
    try:
        content_type = request.headers.get("Content-Type", "")
        content_encoding = request.headers.get("Content-Encoding", "")

        raw_data = request.get_data()

        if content_encoding == "gzip":
            try:
                raw_data = gzip.decompress(raw_data)
            except Exception as e:
                print(f"Failed to decompress gzip data: {e}")

        metrics_data = {
            "timestamp": datetime.now().isoformat(),
            "content_type": content_type,
            "content_encoding": content_encoding,
            "headers": dict(request.headers),
            "data_size": len(raw_data),
        }

        if "application/json" in content_type:
            try:
                json_data = json.loads(raw_data.decode("utf-8"))
                metrics_data["format"] = "json"
                metrics_data["data"] = json_data
            except Exception as e:
                metrics_data["format"] = "json_error"
                metrics_data["error"] = str(e)
                metrics_data["raw_data"] = raw_data.decode("utf-8", errors="ignore")[
                    :500
                ]
        else:
            metrics_data["format"] = "protobuf"
            metrics_data["data"] = decode_protobuf_data(raw_data)

        with data_lock:
            collected_data["metrics"].append(metrics_data)

        if len(collected_data["metrics"]) % 10 == 0:
            save_data_to_file("metrics", collected_data["metrics"])

        print(
            f"Received metrics data: {metrics_data['data_size']} bytes, format: {metrics_data['format']}"
        )

        return "", 200

    except Exception as e:
        print(f"Error processing metrics: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/v1/logs", methods=["POST"])
def receive_logs():
    """Receive OTLP logs data"""
    try:
        content_type = request.headers.get("Content-Type", "")
        content_encoding = request.headers.get("Content-Encoding", "")

        raw_data = request.get_data()

        if content_encoding == "gzip":
            try:
                raw_data = gzip.decompress(raw_data)
            except Exception as e:
                print(f"Failed to decompress gzip data: {e}")

        logs_data = {
            "timestamp": datetime.now().isoformat(),
            "content_type": content_type,
            "content_encoding": content_encoding,
            "headers": dict(request.headers),
            "data_size": len(raw_data),
        }

        if "application/json" in content_type:
            try:
                json_data = json.loads(raw_data.decode("utf-8"))
                logs_data["format"] = "json"
                logs_data["data"] = json_data
            except Exception as e:
                logs_data["format"] = "json_error"
                logs_data["error"] = str(e)
                logs_data["raw_data"] = raw_data.decode("utf-8", errors="ignore")[:500]
        else:
            logs_data["format"] = "protobuf"
            logs_data["data"] = decode_protobuf_data(raw_data)

        with data_lock:
            collected_data["logs"].append(logs_data)

        if len(collected_data["logs"]) % 10 == 0:
            save_data_to_file("logs", collected_data["logs"])

        print(
            f"Received logs data: {logs_data['data_size']} bytes, format: {logs_data['format']}"
        )

        return "", 200

    except Exception as e:
        print(f"Error processing logs: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/data", methods=["GET"])
def get_all_data():
    """Get all collected data in JSON format"""
    with data_lock:
        return jsonify(
            {
                "summary": {
                    "total_traces": len(collected_data["traces"]),
                    "total_metrics": len(collected_data["metrics"]),
                    "total_logs": len(collected_data["logs"]),
                    "last_updated": datetime.now().isoformat(),
                },
                "data": collected_data,
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


@app.route("/api/clear", methods=["POST"])
def clear_data():
    """Clear all collected data"""
    with data_lock:
        collected_data["traces"].clear()
        collected_data["metrics"].clear()
        collected_data["logs"].clear()

    return jsonify(
        {
            "message": "All collected data cleared",
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/export", methods=["GET"])
def export_data():
    """Export all data to files"""
    try:
        timestamp = datetime.now().isoformat().replace(":", "-")

        with data_lock:
            save_data_to_file(f"export_traces_{timestamp}", collected_data["traces"])
            save_data_to_file(f"export_metrics_{timestamp}", collected_data["metrics"])
            save_data_to_file(f"export_logs_{timestamp}", collected_data["logs"])

        return jsonify(
            {
                "message": "Data exported successfully",
                "timestamp": timestamp,
                "files": [
                    f"storage/export_traces_{timestamp}.json",
                    f"storage/export_metrics_{timestamp}.json",
                    f"storage/export_logs_{timestamp}.json",
                ],
            }
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    """Root endpoint with collector information"""
    return jsonify(
        {
            "name": "Fake OpenTelemetry Collector",
            "version": "1.0.0",
            "description": "A Flask-based fake OTEL collector for testing",
            "endpoints": {
                "traces": "/v1/traces",
                "metrics": "/v1/metrics",
                "logs": "/v1/logs",
                "health": "/health",
                "data": "/api/data",
                "export": "/api/export",
                "clear": "/api/clear",
            },
            "supported_formats": ["protobuf", "json"],
            "status": "running",
        }
    )


if __name__ == "__main__":
    print("Starting Fake OpenTelemetry Collector...")
    print("Endpoints available:")
    print("  POST /v1/traces   - Receive trace data")
    print("  POST /v1/metrics  - Receive metrics data")
    print("  POST /v1/logs     - Receive logs data")
    print("  GET  /api/data    - Get all collected data")
    print("  GET  /health      - Health check")
    print("  GET  /            - Collector info")

    app.run(host="0.0.0.0", port=4317, debug=True)
    app.run(host='0.0.0.0', port=4317, debug=True)
