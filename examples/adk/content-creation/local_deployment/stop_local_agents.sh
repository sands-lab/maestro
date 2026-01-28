#!/bin/bash
# Stop all locally running agents

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$SCRIPT_DIR/pids"

echo "=========================================="
echo "Stopping Local Multi-Agent System"
echo "=========================================="

if [ ! -d "$PID_DIR" ]; then
    echo "No PID directory found. Are agents running?"
    exit 0
fi

# Function to stop a process gracefully
stop_process() {
    local name=$1
    local pid_file="$PID_DIR/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        echo "  $name: No PID file found (already stopped?)"
        return
    fi

    local pid=$(cat "$pid_file")

    if ! kill -0 $pid 2>/dev/null; then
        echo "  $name: Process $pid not running (stale PID file)"
        rm -f "$pid_file"
        return
    fi

    echo "  $name: Stopping PID $pid..."
    kill $pid 2>/dev/null || true

    # Wait up to 5 seconds for graceful shutdown
    local count=0
    while kill -0 $pid 2>/dev/null && [ $count -lt 50 ]; do
        sleep 0.1
        count=$((count + 1))
    done

    # Force kill if still running
    if kill -0 $pid 2>/dev/null; then
        echo "  $name: Force killing..."
        kill -9 $pid 2>/dev/null || true
        sleep 0.5
    fi

    rm -f "$pid_file"
    echo "  $name: ✓ Stopped"
}

# Stop all agents in reverse order
stop_process "coordinator"
stop_process "editor"
stop_process "writer"
stop_process "planner"

# Clean up PID directory if empty
if [ -d "$PID_DIR" ] && [ -z "$(ls -A "$PID_DIR")" ]; then
    rmdir "$PID_DIR"
fi

echo ""
echo "✓ All agents stopped"
echo ""
