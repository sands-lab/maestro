# Benchmark Harness

The `mas_traces` directory hosts repeatable benchmark scenarios (semantic cache, tourist scheduler, and the MCP financial analyzer). Every scenario writes OpenTelemetry traces to its own `logs/` folder so you can archive or diff runs.

## Quick start

```bash
cd mas_traces
python run_benchmarks.py --list                         # show available suites
python run_benchmarks.py --benchmark semantic_cache     # single run
python run_benchmarks.py --runs 5 --timeout 300         # all benches, repeated
```

The runner spins up each benchmark sequentially, enforces a per-run timeout (SIGKILL on expiration), and prints the new log files created by that run. You can still execute any benchmark directly by `cd`'ing into its folder and running `python main.py`.

