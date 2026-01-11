#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd -- "${SCRIPT_DIR}/.." && pwd)
CONFIG_PATH="${ROOT_DIR}/otel-collector.local.yaml"
LOG_DIR="${ROOT_DIR}/collector_logs"
mkdir -p "${LOG_DIR}"
chmod 777 "${LOG_DIR}"

RUN_ID="${FINANCIAL_ANALYZER_RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
RUN_LOG_BASENAME="financial_analyzer_spans-${RUN_ID}.jsonl"
RUN_LOG_PATH="${LOG_DIR}/${RUN_LOG_BASENAME}"
LATEST_LOG_PATH="${LOG_DIR}/financial_analyzer_spans.jsonl"

touch "${RUN_LOG_PATH}"
chmod 666 "${RUN_LOG_PATH}"
ln -sfn "${RUN_LOG_BASENAME}" "${LATEST_LOG_PATH}"

IMAGE=${OTEL_COLLECTOR_IMAGE:-"otel/opentelemetry-collector-contrib:latest"}
CONTAINER_NAME=${OTEL_COLLECTOR_CONTAINER_NAME:-"financial-analyzer-otel-collector"}

echo "Launching OpenTelemetry Collector (${IMAGE}) using ${CONFIG_PATH}" >&2
echo "Logs will stream to ${RUN_LOG_PATH} (symlinked at ${LATEST_LOG_PATH})" >&2
echo "Run ID: ${RUN_ID}" >&2
if [[ -z "${FINANCIAL_ANALYZER_RUN_ID:-}" ]]; then
  echo "Tip: export FINANCIAL_ANALYZER_RUN_ID=${RUN_ID} before running the analyzer to reuse this ID." >&2
fi

exec docker run --rm \
  --name "${CONTAINER_NAME}" \
  -p 4317:4317 \
  -p 4318:4318 \
  -v "${CONFIG_PATH}:/etc/otelcol-config.yaml:ro" \
  -v "${LOG_DIR}:/app/collector_logs" \
  "${IMAGE}" --config=/etc/otelcol-config.yaml
