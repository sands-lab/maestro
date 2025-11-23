# Benchmark Harness

The `mas_traces` directory hosts repeatable benchmark scenarios (semantic cache, tourist scheduler, and the MCP financial analyzer). Every scenario writes OpenTelemetry traces to its own `logs/` folder so you can archive or diff runs.

## Quick start

```bash
cd mas_traces
python run_benchmarks.py --list                         # show available suites
python run_benchmarks.py --benchmark semantic_cache     # single run
python run_benchmarks.py --runs 5 --timeout 300         # all benches, repeated
python run_benchmarks.py --llm-rate-limit 2             # optional LLM RPM cap
```

The runner spins up each benchmark sequentially, enforces a per-run timeout (SIGKILL on expiration), and prints the new log files created by that run. Use `--llm-rate-limit` (plus `--llm-rate-period`) to throttle compatible suites during stress tests. You can still execute any benchmark directly by `cd`'ing into its folder and running `python main.py`.
