# Language Agent Tree Search Benchmark

This directory converts the LangChain `lats.ipynb` tutorial into a repeatable CLI benchmark that mirrors the structure used by the other MAS traces (for example `faq_redis_semantic_cache_naive`). Instead of stepping through the notebook manually, you can now replay the Language Agent Tree Search (LATS) workflow—initial candidate generation, Monte-Carlo style expansion, Tavily tool calls, self-reflection, and loop termination—against a list of questions while capturing reproducible logs and metadata.

```
┌─────────────┐   ┌──────────────────┐   ┌───────────┐   ┌───────────────┐
│ Question(s) │ → │ LATS LangGraph   │ → │ Tavily    │ → │ Reflections & │
└─────────────┘   │ (start/expand)   │   │ tool node │   │ scoring       │
                  └──────────────────┘   └───────────┘   └───────────────┘
                                                         │
                                                         ▼
                        logs/run_<ts>.log + run_<ts>.metadata.json + run_<ts>.otel.jsonl
```

## 1. Environment setup

```bash
cd mas_traces/language-agent-tree-search
python -m venv .venv && source .venv/bin/activate  # or use uv
pip install -r requirements.txt
```

Credentials expected by the benchmark:

- `OPENAI_API_KEY` – used by `langchain-openai` for the LATS planner/reflection LLM.
- `TAVILY_API_KEY` – required because the notebook relies on Tavily search results as the only tool.
- (Optional) `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` if you want tracing in LangSmith while running benchmarks.

## 2. Run the benchmark

```bash
python main.py \
  --model gpt-4o-mini \
  --temperature 0.3 \
  --max-depth 5 \
  --branching-factor 5 \
  --questions-file data/questions.csv
```

Each run now writes:

- `logs/run_<timestamp>.log` – human-readable stream of graph events
- `logs/run_<timestamp>.metadata.json` – CLI args, dataset provenance, solved stats, and artifact pointers
- `logs/run_<timestamp>.otel.jsonl` – OpenTelemetry spans following `otel_template/otel_span_template.json`
- `metrics/language-agent-tree-search_<timestamp>.metrics.jsonl` – psutil-derived CPU/RSS snapshots that match `otel_template/otel_metrics_template.json`

The span + metrics files are produced via the shared `mas_traces.langgraph_otel` helpers so they can be reused across other LangGraph examples. You can also invoke this benchmark via the shared runner: `python ../run_benchmarks.py --benchmark language_agent_tree_search`.
Each span includes accumulated `gen_ai.usage.*` counters and message byte sizes captured through LangChain callbacks.

### CLI flags

| Flag | Description |
| --- | --- |
| `--model` | Chat Completions model passed to `ChatOpenAI` (default `gpt-4o-mini`). |
| `--temperature` | Forwarded to both the initial candidate and expansion chains. |
| `--max-depth` | Maximum tree height before the search stops (default `5`). |
| `--branching-factor` | Number of candidate continuations sampled per expansion round (default `5`). |
| `--questions-file` | CSV with a `question` column. Defaults to `data/questions.csv`. |
| `--start-index` | 0-based index in the dataset to start from (default `0`). |
| `--num-questions` | Number of questions to run sequentially (default `1`). |
| `--tavily-max-results` | Cap the Tavily search tool output (default `5`). |
| `--metrics-interval` | Seconds between psutil samples for system metrics (default `15`, override via `LATS_METRICS_INTERVAL_SECONDS`). |

## 3. Project layout

```
mas_traces/language-agent-tree-search
├── data/questions.csv          # Simple default dataset extracted from the notebook
├── logs/                       # Run logs + metadata (gitignored, .gitkeep placeholder)
├── metrics/                    # System metrics snapshots (gitignored)
├── main.py                     # CLI benchmark implementation
├── README.md                   # This guide
├── requirements.txt            # Python dependencies
└── lats.ipynb                  # Original notebook for reference
```

The benchmark stays close to the notebook logic so you can tweak prompts, branching factors, or reward shaping while still producing reproducible artifacts for downstream automation and regression testing.
