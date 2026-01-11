import logging
import os
from datetime import datetime

from flask import Flask, jsonify, request
from flask_cors import CORS

from ..config import *
from .data_handler import DataHandler


def create_app(config=None):
    """Flask app factory"""
    app = Flask(__name__)

    # Configure CORS if enabled
    if ENABLE_CORS:
        CORS(app, origins=CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else "*")

    # Initialize data handler
    data_handler = DataHandler(
        storage_dir=STORAGE_DIR, max_memory_items=MAX_MEMORY_ITEMS
    )

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    def authenticate():
        """Simple API key authentication if enabled"""
        if not ENABLE_AUTH:
            return True

        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        return api_key == API_KEY if API_KEY else True

    @app.before_request
    def before_request():
        """Authentication check before each request"""
        if not authenticate():
            return jsonify({"error": "Invalid or missing API key"}), 401

    @app.route("/", methods=["GET"])
    def index():
        """Root endpoint with collector information"""
        stats = data_handler.get_stats()
        return jsonify(
            {
                "name": "Fake OpenTelemetry Collector",
                "version": "1.0.0",
                "description": "A Flask-based fake OTEL collector for testing and development",
                "endpoints": {
                    "traces": TRACES_ENDPOINT,
                    "metrics": METRICS_ENDPOINT,
                    "logs": LOGS_ENDPOINT,
                    "health": "/health",
                    "data": "/api/data",
                    "export": "/api/export",
                    "clear": "/api/clear",
                    "stats": "/api/stats",
                },
                "supported_formats": ["protobuf", "json"],
                "supported_encodings": SUPPORTED_ENCODINGS,
                "status": "running",
                "stats": stats,
            }
        )

    @app.route("/health", methods=["GET"])
    def health_check():
        """Health check endpoint"""
        stats = data_handler.get_stats()
        return jsonify(
            {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": stats.get("uptime_seconds", 0),
                "collected_data": stats.get("current_memory_usage", {}),
                "total_received": {
                    "traces": stats.get("traces_received", 0),
                    "metrics": stats.get("metrics_received", 0),
                    "logs": stats.get("logs_received", 0),
                    "errors": stats.get("errors", 0),
                },
            }
        )

    @app.route(TRACES_ENDPOINT, methods=["POST"])
    def receive_traces():
        """Receive OTLP traces data"""
        try:
            raw_data = request.get_data()
            headers = dict(request.headers)

            processed_data = data_handler.process_data("traces", raw_data, headers)

            logger.info(f"Received trace data: {processed_data['data_size']} bytes")
            return "", 200

        except Exception as e:
            logger.error(f"Error processing traces: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route(METRICS_ENDPOINT, methods=["POST"])
    def receive_metrics():
        """Receive OTLP metrics data"""
        try:
            raw_data = request.get_data()
            headers = dict(request.headers)

            processed_data = data_handler.process_data("metrics", raw_data, headers)

            logger.info(f"Received metrics data: {processed_data['data_size']} bytes")
            return "", 200

        except Exception as e:
            logger.error(f"Error processing metrics: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route(LOGS_ENDPOINT, methods=["POST"])
    def receive_logs():
        """Receive OTLP logs data"""
        try:
            raw_data = request.get_data()
            headers = dict(request.headers)

            processed_data = data_handler.process_data("logs", raw_data, headers)

            logger.info(f"Received logs data: {processed_data['data_size']} bytes")
            return "", 200

        except Exception as e:
            logger.error(f"Error processing logs: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/data", methods=["GET"])
    def get_all_data():
        """Get all collected data in JSON format"""
        limit = request.args.get("limit", type=int)
        data = data_handler.get_data(limit=limit)
        stats = data_handler.get_stats()

        return jsonify(
            {
                "summary": {
                    "total_traces": stats.get("current_memory_usage", {}).get(
                        "traces", 0
                    ),
                    "total_metrics": stats.get("current_memory_usage", {}).get(
                        "metrics", 0
                    ),
                    "total_logs": stats.get("current_memory_usage", {}).get("logs", 0),
                    "last_updated": datetime.now().isoformat(),
                },
                "data": data,
            }
        )

    @app.route("/api/traces", methods=["GET"])
    def get_traces():
        """Get only traces data"""
        limit = request.args.get("limit", type=int, default=100)
        data = data_handler.get_data("traces", limit=limit)
        return jsonify(data)

    @app.route("/api/metrics", methods=["GET"])
    def get_metrics():
        """Get only metrics data"""
        limit = request.args.get("limit", type=int, default=100)
        data = data_handler.get_data("metrics", limit=limit)
        return jsonify(data)

    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        """Get only logs data"""
        limit = request.args.get("limit", type=int, default=100)
        data = data_handler.get_data("logs", limit=limit)
        return jsonify(data)

    @app.route("/api/stats", methods=["GET"])
    def get_stats():
        """Get collection statistics"""
        return jsonify(data_handler.get_stats())

    @app.route("/api/clear", methods=["POST"])
    def clear_data():
        """Clear collected data"""
        data_type = request.json.get("type") if request.is_json else None
        data_handler.clear_data(data_type)

        message = f"Cleared {data_type} data" if data_type else "Cleared all data"
        return jsonify({"message": message, "timestamp": datetime.now().isoformat()})

    @app.route("/api/export", methods=["GET", "POST"])
    def export_data():
        """Export all data to files"""
        try:
            export_info = data_handler.export_data()
            return jsonify(
                {"message": "Data exported successfully", "export_info": export_info}
            )
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/cleanup", methods=["POST"])
    def cleanup_files():
        """Clean up old export files"""
        try:
            retention_hours = (
                request.json.get("retention_hours", DATA_RETENTION_HOURS)
                if request.is_json
                else DATA_RETENTION_HOURS
            )
            cleaned_files = data_handler.cleanup_old_files(retention_hours)

            return jsonify(
                {
                    "message": f"Cleaned up {len(cleaned_files)} old files",
                    "cleaned_files": cleaned_files,
                    "retention_hours": retention_hours,
                }
            )
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
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

    return app
