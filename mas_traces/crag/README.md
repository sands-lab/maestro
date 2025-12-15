# Corrective RAG (CRAG) Benchmark

Command-line port of the `langgraph_crag.ipynb` tutorial. The script under
`main.py` recreates the graph that:

1. Loads a few Lilian Weng blog posts, chunks them into embeddings, and retrieves
   candidate documents for each question.
2. Grades the retrieved passages and falls back to the CRAG remediation loop
   (rewrite the query + issue a Tavily search) when everything is irrelevant.
3. Generates the final answer with the retrieved context.

All LangGraph nodes wrap their LLM/tool calls with
`mas_traces.langgraph_otel.run_llm_with_span` / `run_tool_with_span` so each step
emits consistent OpenTelemetry `gen_ai.operation.name` attributes, and every run
produces `logs/run_<timestamp>.log`, `run_<timestamp>.metadata.json`, system
metrics, and `.otel.jsonl` traces. The CLI follows the same conventions as the
Tree-of-Thoughts and Plan-and-Execute examples (psutil sampling, `invoke_agent`
spans per question, etc.).

## Quick start

```bash
cd mas_traces/crag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...  # optional if you run with --disable-web-search
python main.py --question "How does the AlphaCodium paper work?"
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
