# Corrective RAG (CRAG) Benchmark

Command-line port of the `langgraph_crag.ipynb` tutorial. The script under
`main.py` recreates the graph that:

1. Loads a few Lilian Weng blog posts, chunks them into embeddings, and retrieves
   candidate documents for each question.
2. Grades the retrieved passages and falls back to the CRAG remediation loop
   (rewrite the query + issue a Tavily search) when everything is irrelevant.
3. Generates the final answer with the retrieved context.

All LangGraph nodes wrap their LLM/tool calls with
`plugin_monitoring.langgraph_otel.run_llm_with_span` / `run_tool_with_span` so each step
emits consistent OpenTelemetry `gen_ai.operation.name` attributes, and every run
produces `logs/run_<timestamp>.log`, `run_<timestamp>.metadata.json`, system
metrics, and `.otel.jsonl` traces. The CLI follows the same conventions as the
Tree-of-Thoughts and Plan-and-Execute examples (psutil sampling, `invoke_agent`
spans per question, etc.).

## Quick start

```bash
cd examples/langgraph/crag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...  # optional if you run with --disable-web-search
python main.py --question "How does the AlphaCodium paper work?"
```

## 2. Run the benchmark

Batch runs over a dataset use the CLI flags below (any `.txt` or `.csv`
question file works as long as the CSV has a `question` column):

```bash
python main.py \
  --questions-file ../Plan-and-Execute/data/hotpot_dev_questions.csv \
  --metrics-interval 0.5 \
  --start-index 0 \
  --num-questions 2
```

Use Gemini via Vertex AI (requirements include `langchain-google-vertexai`; set `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_REGION` or pass flags):

```bash
python main.py \
  --provider google-vertex \
  --vertex-project $GOOGLE_CLOUD_PROJECT \
  --vertex-location us-central1 \
  --generator-model gemini-2.5-flash-lite \
  --grader-model gemini-2.5-flash-lite \
  --rewriter-model gemini-2.5-flash-lite \
  --embedding-model text-embedding-3-small \
  --questions-file ../Plan-and-Execute/data/hotpot_dev_questions.csv
```

Enable LLM-as-judge correctness scoring (default evaluator is F1):

```bash
  --evaluator llm --judge-model gpt-4o-mini --judge-provider openai --run-timeout-seconds 300
```

Key CLI flags:

- `--questions-file questions.csv --num-questions 5` – iterate through a dataset
  (either `.txt` lines or `.csv` with a `question` column).
- `--seed-url URL` – override the default Lilian Weng blog posts with any number
  of custom retrieval sources (repeat the flag for multiple URLs).
- `--generator-model/--grader-model/--rewriter-model` – choose different OpenAI
  chat models per CRAG phase; `--embedding-model` controls the retriever.
- `--disable-web-search` – turns Tavily off entirely if you only want the
  retrieval grader loop.

Each run writes summaries under `logs/` plus metadata that references the trace
and metrics files so you can import them into the shared
`otel_template/otel_span_template.json` dashboard or diff results with
`run_benchmarks.py`.

## MAS agent-outcome metrics

CRAG is wired into the same MAS-specific OTEL attributes as the other LangGraph benchmarks. The shared table makes it easy to confirm which scenarios currently populate each field:

| Attribute | Tree-of-Thoughts | Language Agent Tree Search | CRAG | Plan-and-Execute | Notes |
| --- | --- | --- | --- | --- | --- |
| `agent.retry.attempt_number` + friends | `[x]` | `[x]` | `[x]` | `[x]` | CRAG increments retry counters when the retrieval grader forces a rewrite or Tavily remediation; we also propagate the previous span id through `search_previous_span_id` so the next span points back to the failure. |
| `agent.failure.category` / `agent.failure.reason` | `[x]` | `[x]` | `[x]` | `[x]` | Tavily spans mark `category=quality` when no new snippets are appended; other nodes can tag `system`, `timeout`, etc. if their operations fail. |
| `agent.output.useless` (+ `_reason`) | `[x]` | `[x]` | `[x]` | `[x]` | CRAG flags useless web-search spans when zero usable documents come back and clears the flag after successful enrichment. |

Implementation reminders:

- The LangGraph state carries `search_retry_attempts` + `search_previous_span_id` so the retrieval, rewrite, Tavily, and generation nodes all share the same context.
- `plugin_monitoring.langgraph_otel.set_agent_failure_attributes` / `set_agent_usefulness` keep the OTEL attribute names consistent—use those helpers (as the current `main.py` does) whenever you extend the CRAG graph with additional remediation steps.
- Future benchmarks should follow the same pattern: store per-node retry counters in state, pass an `AgentCallContext` into `run_llm_with_span` / `run_tool_with_span`, and record the span id via `span_id_hex` so chained retries have breadcrumbs.

## Telemetry comparison helper

The generic scorecard/plot script `tools/telemetry_compare.py` can summarize CRAG vs other agent architectures. Key flags (run from `tools/` with the venv):

```bash
./.venv/bin/python telemetry_compare.py \
  --config runs_wi_by_model.json \
  --out-csv ../analysis/telemetry/tables/scorecard_wi_by_model.csv \
  --plot-output ../analysis/telemetry/plots/tradeoff_scorecard_wi_by_model_overview.pdf \
  --accuracy-plot-output ../analysis/telemetry/plots/accuracy_scorecard_wi_by_model.pdf \
  --plot-mode overview \
  --skip-crowded-labels \
  --label-distance-threshold 0.18
```

- `--plot-mode` (`overview` or `facet-by-model`) controls layout.
- `--skip-crowded-labels` + `--label-distance-threshold` hide labels whose nearest neighbor is too close (to reduce overlap on the overview scatter).
- `--accuracy-plot-output` writes a separate accuracy bar chart.
