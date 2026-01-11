# Local Deployment Guide

This directory contains scripts for running the multi-agent system locally (non-containerized).

## Quick Start

### 1. Setup Environment

```bash
# Copy example config
cp .env.example .env

# Edit if needed (default values work out of the box)
nano .env
```

### 2. Start All Agents

```bash
./start_local_agents.sh
```

This will start 4 processes:
- **Content Planner Agent** (port 10001)
- **Content Writer Agent** (port 10002)
- **Content Editor Agent** (port 10003)
- **Coordinator API** (port 8083, A2A API without UI)

### 3. Verify Services

```bash
# Check health
curl http://localhost:10001/.well-known/agent-card.json
curl http://localhost:10002/.well-known/agent-card.json
curl http://localhost:10003/.well-known/agent-card.json
curl http://localhost:8083/.well-known/agent-card.json
```

### 4. View Logs

```bash
# All logs
tail -f logs/*_<timestamp>.log

# Individual service
tail -f logs/coordinator_<timestamp>.log
tail -f logs/planner_<timestamp>.log
```

### 5. Stop All Agents

```bash
./stop_local_agents.sh
```

## Configuration

Edit `.env` to customize:

```bash
# Ports
PLANNER_PORT=10001
WRITER_PORT=10002
EDITOR_PORT=10003
COORDINATOR_PORT=8083
```

## Directory Structure

```
local_deployment/
├── .env.example          # Example configuration
├── .env                  # Your configuration (gitignored)
├── start_local_agents.sh # Start all agents
├── stop_local_agents.sh  # Stop all agents
├── README.md             # This file
├── logs/                 # Runtime logs (auto-created)
└── pids/                 # Process IDs (auto-created)
```

## Troubleshooting

### Agent fails to start

Check the logs:
```bash
tail -50 logs/<agent>_<timestamp>.log
```

Common issues:
1. **Port already in use**: Change port in `.env`
2. **Missing dependencies**: Install from parent directory:
   ```bash
   cd ../src/agents/content_planner && pip install -e .
   cd ../content_writer && pip install -e .
   cd ../content_editor && pip install -e .
   cd ../../hosts/coordinator && pip install -e .
   ```
3. **Python not found**: Ensure you're in the right conda/venv environment

### Process hangs

Force kill all:
```bash
pkill -f "python -m content_planner"
pkill -f "python -m content_writer"
pkill -f "python -m content_editor"
pkill -f "python -m coordinator"
```

### Port conflicts

Check what's using a port:
```bash
lsof -i :8083   # Coordinator
lsof -i :10001  # Planner
```

## Performance Notes

Local deployment characteristics:
- **No container overhead**: Direct process execution
- **No network virtualization**: Localhost IPC is fast
- **No orchestration overhead**: Direct local processes
- **Single-machine limits**: Cannot scale beyond one node
- **Shared resources**: All processes compete for CPU/memory

Expected use cases:
1. **Development**: Fast iteration without Docker builds
2. **Baseline testing**: Compare runs or configs
3. **Debugging**: Easy access to logs and process inspection
4. **Resource-constrained environments**: Run on a single machine
