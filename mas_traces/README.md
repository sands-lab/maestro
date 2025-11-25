# Benchmark Harness

The `mas_traces` directory hosts repeatable benchmark scenarios (semantic cache, tourist scheduler, and the MCP financial analyzer). Every scenario writes OpenTelemetry traces to its own `logs/` folder so you can archive or diff runs.

## Quick start

```bash
cd mas_traces
python run_benchmarks.py --list                                # show available suites
python run_benchmarks.py --benchmark faq_redis_semantic_cache_naive       # single run
python run_benchmarks.py --runs 5 --timeout 300                # all benches, repeated
python run_benchmarks.py --llm-rate-limit 2                    # optional LLM RPM cap
```

The runner spins up each benchmark sequentially, enforces a per-run timeout (SIGKILL on expiration), and prints the new log files created by that run. Use `--llm-rate-limit` (plus `--llm-rate-period`) to throttle compatible suites during stress tests. You can still execute any benchmark directly by `cd`'ing into its folder and running `python main.py`.

## Example Progress Checklist

Below is the progress status for each benchmark example. This list will expand as new examples are added.

- [x] **financial_analyzer**: Runs successfully with OpenAI LLM and Tavily search MCP.
- [ ] **faq_redis_semantic_cache_naive**: Not yet tested (single-agent/FAQ Redis cache baseline).
- [ ] **tourist_scheduler**: Not yet tested.
