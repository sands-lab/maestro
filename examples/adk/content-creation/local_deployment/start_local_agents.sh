#!/bin/bash
# Start all agents locally (non-containerized deployment)
# Each agent runs as a separate background process

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment variables (supports inline comments)
load_env_file() {
    local env_file=$1
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
}

if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from .env..."
    load_env_file "$SCRIPT_DIR/.env"
else
    echo "Warning: .env not found, using defaults from .env.example"
    load_env_file "$SCRIPT_DIR/.env.example"
fi

# Create logs directory
LOG_DIR="$SCRIPT_DIR/logs"
PID_DIR="$SCRIPT_DIR/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

# Timestamp for log files
TS=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "Starting Local Multi-Agent System"
echo "=========================================="
echo "Project root: $PROJECT_ROOT"
echo "Logs: $LOG_DIR"
echo "PIDs: $PID_DIR"
echo ""

# Function to start an agent
start_agent() {
    local name=$1
    local workdir=$2
    local module=$3
    local host=$4
    local port=$5
    local extra_args=$6

    echo "Starting $name on $host:$port..."

    cd "$workdir"

    # Start the agent in background
    # Pass OTEL_RUN_TIMESTAMP if set (for unified timestamp across all agents)
    if [ -n "$extra_args" ]; then
        nohup env OTEL_RUN_TIMESTAMP="${OTEL_RUN_TIMESTAMP:-}" python -m "$module" --host "$host" --port "$port" $extra_args \
            > "$LOG_DIR/${name}_${TS}.log" 2>&1 &
    else
        nohup env OTEL_RUN_TIMESTAMP="${OTEL_RUN_TIMESTAMP:-}" python -m "$module" --host "$host" --port "$port" \
            > "$LOG_DIR/${name}_${TS}.log" 2>&1 &
    fi

    local pid=$!
    echo $pid > "$PID_DIR/${name}.pid"
    echo "  ✓ Started with PID: $pid"

    # Wait a moment for startup
    sleep 1

    # Check if process is still running
    if ! kill -0 $pid 2>/dev/null; then
        echo "  ✗ ERROR: Process died immediately! Check logs:"
        echo "    tail -50 $LOG_DIR/${name}_${TS}.log"
        return 1
    fi
}

# Function to start coordinator (API mode, no UI)
start_coordinator() {
    echo "Starting Coordinator API on $COORDINATOR_HOST:$COORDINATOR_PORT..."

    cd "$PROJECT_ROOT/src/hosts/coordinator"

    # Ensure environment variables are passed
    export PROVIDER=${PROVIDER:-ollama}
    export MODEL=${MODEL:-gpt-3.5-turbo}
    export OLLAMA_API_BASE=${OLLAMA_API_BASE:-http://localhost:11434/v1}
    export OLLAMA_API_KEY=${OLLAMA_API_KEY:-dummy}
    export CONTENT_PLANNER_AGENT_URL=${CONTENT_PLANNER_AGENT_URL:-http://127.0.0.1:10001}
    export CONTENT_WRITER_AGENT_URL=${CONTENT_WRITER_AGENT_URL:-http://127.0.0.1:10002}
    export CONTENT_EDITOR_AGENT_URL=${CONTENT_EDITOR_AGENT_URL:-http://127.0.0.1:10003}

    # Use the API-only version without Gradio UI
    # Pass OTEL_RUN_TIMESTAMP if set (for unified timestamp across all agents)
    nohup env OTEL_RUN_TIMESTAMP="${OTEL_RUN_TIMESTAMP:-}" python -m coordinator.__main_api__ --host "$COORDINATOR_HOST" --port "$COORDINATOR_PORT" \
        > "$LOG_DIR/coordinator_${TS}.log" 2>&1 &

    local pid=$!
    echo $pid > "$PID_DIR/coordinator.pid"
    echo "  ✓ Started with PID: $pid"
    sleep 2

    if ! kill -0 $pid 2>/dev/null; then
        echo "  ✗ ERROR: Coordinator died immediately! Check logs:"
        echo "    tail -50 $LOG_DIR/coordinator_${TS}.log"
        return 1
    fi
}

# Cleanup function
cleanup_on_error() {
    echo ""
    echo "Error during startup. Cleaning up..."
    "$SCRIPT_DIR/stop_local_agents.sh"
    exit 1
}

trap cleanup_on_error ERR

# ============================================
# Start all components
# ============================================

# 1. Content Planner Agent
start_agent "planner" \
    "$PROJECT_ROOT/src/agents/content_planner" \
    "content_planner" \
    "$PLANNER_HOST" \
    "$PLANNER_PORT"

# 2. Content Writer Agent
start_agent "writer" \
    "$PROJECT_ROOT/src/agents/content_writer" \
    "content_writer" \
    "$WRITER_HOST" \
    "$WRITER_PORT"

# 3. Content Editor Agent
start_agent "editor" \
    "$PROJECT_ROOT/src/agents/content_editor" \
    "content_editor" \
    "$EDITOR_HOST" \
    "$EDITOR_PORT"

# Wait for agents to be fully ready before starting coordinator
echo "Waiting for agents to stabilize (10s)..."
sleep 10

# 4. Coordinator (API mode)
start_coordinator

echo ""
echo "=========================================="
echo "✓ All agents started successfully!"
echo "=========================================="
echo ""
echo "Services:"
echo "  Planner:     http://$PLANNER_HOST:$PLANNER_PORT"
echo "  Writer:      http://$WRITER_HOST:$WRITER_PORT"
echo "  Editor:      http://$EDITOR_HOST:$EDITOR_PORT"
echo "  Coordinator: http://$COORDINATOR_HOST:$COORDINATOR_PORT"
echo ""
echo "Health checks:"
sleep 1
curl -s http://$PLANNER_HOST:$PLANNER_PORT/.well-known/agent-card.json > /dev/null && echo "  ✓ Planner: OK" || echo "  ✗ Planner: FAILED"
curl -s http://$WRITER_HOST:$WRITER_PORT/.well-known/agent-card.json > /dev/null && echo "  ✓ Writer: OK" || echo "  ✗ Writer: FAILED"
curl -s http://$EDITOR_HOST:$EDITOR_PORT/.well-known/agent-card.json > /dev/null && echo "  ✓ Editor: OK" || echo "  ✗ Editor: FAILED"
curl -s http://$COORDINATOR_HOST:$COORDINATOR_PORT/.well-known/agent-card.json > /dev/null && echo "  ✓ Coordinator: OK" || echo "  ✗ Coordinator: FAILED"

echo ""
echo "Logs:"
echo "  View all: tail -f $LOG_DIR/*_${TS}.log"
echo "  Planner:  tail -f $LOG_DIR/planner_${TS}.log"
echo "  Writer:   tail -f $LOG_DIR/writer_${TS}.log"
echo "  Editor:   tail -f $LOG_DIR/editor_${TS}.log"
echo "  Coordinator: tail -f $LOG_DIR/coordinator_${TS}.log"
echo ""
echo "To stop all agents: $SCRIPT_DIR/stop_local_agents.sh"
echo ""
