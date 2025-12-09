#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
CONFIG_PATH="${ROOT_DIR}/otel-collector.local.yaml"
LOG_DIR="${ROOT_DIR}/collector_logs"

mkdir -p "${LOG_DIR}"
chmod 777 "${LOG_DIR}"
touch "${LOG_DIR}/financial_analyzer_spans.jsonl"
chmod 666 "${LOG_DIR}/financial_analyzer_spans.jsonl"

IMAGE=${OTEL_COLLECTOR_IMAGE:-"otel/opentelemetry-collector-contrib:latest"}
CONTAINER_NAME=${OTEL_COLLECTOR_CONTAINER_NAME:-"financial-analyzer-otel-collector"}

echo "Launching OpenTelemetry Collector (${IMAGE}) using ${CONFIG_PATH}" >&2
echo "Logs will stream to ${LOG_DIR}" >&2

exec docker run --rm \
  --name "${CONTAINER_NAME}" \
  -p 4317:4317 \
  -p 4318:4318 \
  -v "${CONFIG_PATH}:/etc/otelcol-config.yaml:ro" \
  -v "${LOG_DIR}:/app/collector_logs" \
  "${IMAGE}" --config=/etc/otelcol-config.yaml
