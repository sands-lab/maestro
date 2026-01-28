#!/bin/bash
# Quick verification script for local deployment setup

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Local Deployment Quick Verification"
echo "=========================================="
echo ""

# Check if required Python modules can be imported
echo "1. Checking Python modules..."

check_module() {
    local dir=$1
    local module=$2

    cd "$dir"
    if python -c "import $module" 2>/dev/null; then
        echo "  ✓ $module importable"
        return 0
    else
        echo "  ✗ $module NOT importable"
        echo "    Install with: cd $dir && pip install -e ."
        return 1
    fi
}

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

check_module "$PROJECT_ROOT/src/agents/content_planner" "content_planner" || true
check_module "$PROJECT_ROOT/src/agents/content_writer" "content_writer" || true
check_module "$PROJECT_ROOT/src/agents/content_editor" "content_editor" || true
check_module "$PROJECT_ROOT/src/hosts/coordinator" "coordinator" || true

echo ""
echo "2. Checking port availability..."

check_port() {
    local port=$1
    local name=$2

    if lsof -i :$port > /dev/null 2>&1; then
        echo "  ✗ Port $port ($name) already in use"
        echo "    Kill with: lsof -ti :$port | xargs kill"
        return 1
    else
        echo "  ✓ Port $port ($name) available"
        return 0
    fi
}

check_port 8083 "Coordinator" || true
check_port 10001 "Planner" || true
check_port 10002 "Writer" || true
check_port 10003 "Editor" || true

echo ""
echo "3. Checking configuration..."

if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "  ✓ .env file exists"
else
    echo "  ⚠️  .env not found (will use .env.example defaults)"
    echo "    Create with: cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
fi

echo ""
echo "=========================================="
echo "Verification complete!"
echo "=========================================="
echo ""
echo "To start local deployment:"
echo "  cd $SCRIPT_DIR"
echo "  ./start_local_agents.sh"
echo ""
