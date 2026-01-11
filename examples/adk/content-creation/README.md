# Content Creation (ADK)

Local-only multi-agent content creation example. The flow is planner -> writer -> editor, coordinated by a local coordinator API. It uses a real LLM configured in `local_deployment/.env`.

## Directory layout

- `run_experiment.sh`: Run multiple local iterations and collect traces/metrics.
- `send_request.py`: Send a single request to the coordinator.
- `inputs.txt`: Prompt pool used by `run_experiment.sh` (random per run).
- `local_deployment/`: Start/stop scripts and `.env` configuration.
- `src/`: Agent and coordinator code.
- `metrics/`, `traces/`: Aggregated outputs across runs.
- `trash/`: Archived files not used by local real-LLM runs.

## Quick start

1) Configure the real LLM

```bash
cd /home/tiem/agent-observability/examples/adk/content-creation
cp local_deployment/.env.example local_deployment/.env
# Edit PROVIDER/MODEL/API settings in local_deployment/.env
```

2) Run the experiment script

```bash
./run_experiment.sh
```

Useful env overrides:

```bash
ITERATIONS=5 INPUT_COUNT=200 REGENERATE_INPUTS=true ./run_experiment.sh
```

3) Or run agents manually and send one request

```bash
cd local_deployment
./start_local_agents.sh
```

```bash
cd ..
python3 send_request.py --message "Write a short LinkedIn post about agent observability."
```

```bash
cd local_deployment
./stop_local_agents.sh
```

## Outputs

- Run results: `results/exp_<timestamp>_local/`
- Aggregated traces: `traces/`
- Aggregated metrics: `metrics/`
