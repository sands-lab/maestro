# Tourist Scheduling Benchmark

This benchmark distills AGNTCY's [tourist scheduling system](https://github.com/agntcy/agentic-apps/tree/main/tourist_scheduling_system) into a single runnable scenario suitable for automated performance testing. Instead of spinning up the full A2A stack, the script focuses on the greedy scheduling logic, preference scoring, and OpenTelemetry instrumentation so you can measure assignment quality under different market conditions.

```text
┌──────────────┐   load   ┌─────────────┐   adjust   ┌─────────────────┐   summarize
│ guide offers │ ───────▶ │ market shim │ ─────────▶ │ greedy scheduler │ ─────────▶ metrics + OTel log
└──────────────┘          └─────────────┘            └─────────────────┘
```

## 1. Install dependencies

```bash
cd mas_traces/tourist_scheduler_benchmark
python -m venv .venv && source .venv/bin/activate  # or use uv
pip install -r requirements.txt
```

## 2. Run the benchmark

```bash
python main.py --demand-index 1.15 --min-preference-score 1
```

Key flags:

| Flag | Description |
| --- | --- |
| `--demand-index` | Multiplies guide hourly rates to simulate high or low demand |
| `--min-preference-score` | Require a minimum category overlap between tourists and guides |
| `--data-dir` | Point to alternate `guides.json` / `tourists.json` datasets |
| `--log-dir` | Custom directory for the OpenTelemetry span dump |

Console output shows which tourists were matched, their total cost, and summary metrics (assignments, fill rate, etc.). Every run also writes a JSON-lines OpenTelemetry trace under `logs/` so you can diff runs or share artifacts with other benchmark suites.

Use the shared harness to automate multiple passes with a timeout:

```bash
cd mas_traces
python run_benchmarks.py --benchmark tourist_scheduler --runs 5 --timeout 120
```

## 3. Project layout

```
mas_traces/tourist_scheduler_benchmark
├── data/                 # Sample guide + tourist datasets
├── scheduler/            # Models + greedy scheduling engine
├── logs/                 # Run-specific OTel files (gitignored)
├── main.py               # CLI benchmark entry point
├── otel.py               # FileSpanExporter helper
├── requirements.txt
└── sample_output.md
```

## 4. Extending

- Add more guides/tourists by editing the JSON files
- Adjust the greedy strategy in `scheduler/engine.py` to experiment with better scoring heuristics
- Point observability tools at the emitted trace logs to integrate with your existing OTel pipelines
