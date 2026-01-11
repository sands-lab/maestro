# FAQ Redis Semantic Cache (Naive, No MAS)

This directory transforms the `Semantic Caching/L2.ipynb` notebook into a single-agent benchmark that mirrors the structure of the [MCP financial analyzer example](https://github.com/lastmile-ai/mcp-agent/tree/main/examples/usecases/mcp_financial_analyzer). It demonstrates:

> Naive FAQ answering with a Redis-backed semantic cache—no multi-agent workflows required.

1. Building an in-memory semantic cache with `sentence-transformers`
2. Loading the FAQ answers into Redis using `redisvl`
3. Measuring naive (non-MAS) cache hit/miss latency and LLM costs with a lightweight evaluator

```text
┌────────────────────┐        ┌───────────────────────┐        ┌───────────────────────┐
│ FAQ dataset loader │ ───▶── │ In-memory cache demo │ ───▶── │ Redis semantic cache  │
└────────────────────┘        └───────────────────────┘        └───────────────────────┘
                                                                  │
                                                                  ▼
                                                          LLM benchmark + TTL
```

## 1. Environment setup

```bash
cd examples/langgraph/faq_redis_semantic_cache_naive
python -m venv .venv && source .venv/bin/activate  # or use uv
pip install -r requirements.txt
```

Additional prerequisites:

- A running Redis instance (e.g., `redis-stack-server`)
- An OpenAI API key exported as `OPENAI_API_KEY`
- (Optional) The benchmark now emits OpenTelemetry traces, so the only extra requirement is ensuring `logs/` is writable

## 2. Run the benchmark

```bash
python main.py
```

CLI flags:

| Flag | Description |
| --- | --- |
| `--redis-url` | Override the Redis connection string (default `redis://localhost:6379`) |
| `--distance-threshold` | Cosine distance threshold for cache hits (default `0.3`) |
| `--llm-model` | Model used for cache misses (default `gpt-4o-mini`) |
| `--ttl-seconds` | TTL applied to Redis cache entries (default `86400`) |
| `--skip-redis` | Only run the in-memory cache warm-up |
| `--llm-rate-limit` | Optional LLM calls per minute throttle (same via `BENCHMARK_LLM_REQUESTS_PER_MIN`) |

The script prints the in-memory cache behavior, loads the full FAQ dataset into Redis, and then replays benchmark questions. Cache misses trigger an OpenAI call whose latency is tracked by `PerfEval`. Every execution also writes a run-specific OpenTelemetry trace (text format) under `logs/` along with a `.metadata.json` sidecar that captures the runtime parameters (LLM model, Redis URL, rate limits, status, etc.) so you can diff runs later—even failed runs still produce metadata. Use `python ../run_benchmarks.py --benchmark faq_redis_semantic_cache_naive --runs 3` if you want the shared harness to execute repeated runs with timeouts. A sample console log is available in `sample_output.md`. Missing metadata for older traces can be backfilled via `python scripts/backfill_trace_metadata.py --logs-dir logs`.

## 3. Project layout

```
examples/langgraph/faq_redis_semantic_cache_naive
├── cache/                 # Helper modules extracted from the notebook
├── data/                  # FAQ + benchmark query CSV files
├── logs/                  # OpenTelemetry span dumps (gitignored)
├── main.py                # End-to-end benchmark runner
├── requirements.txt       # Python dependencies
└── sample_output.md       # Captured run to verify expectations
```

## 4. Extending the benchmark

- Swap the FAQ dataset by replacing the CSV files under `data/`
- Customize cache warm-up prompts by editing `_demo_queries` in `main.py`
- Point `--redis-url` to a managed Redis deployment to test remote latency

This structure makes it easy to plug the benchmark into CI, perf suites, or MCP-style demos without relying on Jupyter notebooks.

## 5. TODO

- Instrument the Redis server/cache layer with OpenTelemetry so cache operations appear in the traces alongside the Python benchmark spans.
