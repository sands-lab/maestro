# Plan-and-Execute Benchmark

This directory turns the original `Plan-and-Execute.ipynb` notebook into a runnable benchmark that mirrors the structure used by other MAS trace examples (e.g., `faq_redis_semantic_cache_naive`). It showcases how to build a LangGraph **plan → execute → re-plan** agent that relies on:

1. `ChatOpenAI` for the planner, executor, and re-planner roles
2. `TavilySearchResults` as the external search tool surfaced to the agent
3. The LangGraph compiler to orchestrate planner, executor, and re-planner nodes with OpenTelemetry spans + psutil metrics emitted via the shared `plugin_monitoring.langgraph_otel` helpers.

Every CLI run writes node-by-node logs under `logs/`, a matching `.metadata.json`, structured OTEL spans, and psutil-derived system metrics (`metrics/`) so you can diff behavior exactly like the ToT and LATS benchmarks.

```
┌────────────┐      ┌──────────────┐      ┌──────────────┐
│   Planner  │ ───▶ │   Executor   │ ───▶ │  Re-planner  │
└────────────┘      └──────────────┘      └──────────────┘
     ▲                                            │
     └────────────────────────────────────────────┘
```

## 1. Environment setup

```bash
cd examples/langgraph/Plan-and-Execute
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

> Always keep the dependencies inside this `.venv` so the example stays self-contained.

You must also provide valid API keys inside the same shell session:

```bash
export OPENAI_API_KEY=sk-...
export TAVILY_API_KEY=tvly-...
```

## 2. Run the benchmark

```bash
python main.py \
  --questions-file data/hotpot_dev_questions.csv \
  --metrics-interval 0.5 \
  --start-index 0 \
  --num-questions 2 \
  --evidence-source dataset
```

Useful flags:

| Flag | Description |
| ---- | ----------- |
| `--question` | Objective passed to the agent (default: Australian Open hometown example) |
| `--questions-file` | CSV or plaintext list of questions with optional reference passages |
| `--start-index` / `--num-questions` | Control which slice of the dataset to execute |
| `--evidence-source` | `tavily` (default) hits Tavily; `dataset` injects provided references |
| `--executor-model` | Model powering the ReAct executor (`gpt-4o-mini` by default) |
| `--planner-model` / `--replanner-model` | Models that craft/refresh the plan (default: `gpt-4o`) |
| `--max-search-results` | Controls Tavily's breadth per tool call (default: `3`) |
| `--recursion-limit` | LangGraph recursion limit to constrain re-planning loops (default: `50`) |
| `--agent-temperature` | Sampling temperature for the executor (`0` by default) |
| `--prompt` | System prompt for the executor |
| `--quiet` | Skip console summaries—useful when running via `run_benchmarks.py` |
| `--metrics-interval` | Seconds between psutil samples for `metrics/*.metrics.jsonl` (default `15`) |

A sample console transcript is available in `sample_output.md`.

### Sampling HotpotQA locally

Use the helper under `tools/` to downsample the official HotpotQA dumps into a CSV with `question`, `answer`, and gold/distractor passages:

```bash
cd examples/langgraph/Plan-and-Execute
python tools/sample_hotpot_questions.py \
  --source data/hotpot_dev_fullwiki_v1.json \
  --dest data/hotpot_dev_questions.csv \
  --sample-size 200
```

Run the benchmark against that CSV with dataset-provided evidence via:

```bash
python main.py \
  --questions-file data/hotpot_dev_questions.csv \
  --evidence-source dataset \
  --num-questions 5
```

Using Gemini via Vertex AI (requires `langchain-google-vertexai`, already in requirements, plus `GOOGLE_CLOUD_PROJECT` and optional `GOOGLE_CLOUD_REGION`):

```bash
python main.py \
  --provider google-vertex \
  --vertex-project $GOOGLE_CLOUD_PROJECT \
  --vertex-location us-central1 \
  --executor-model gemini-1.5-flash \
  --planner-model gemini-1.5-flash \
  --replanner-model gemini-1.5-flash \
  --questions-file data/hotpot_dev_questions.csv \
  --evidence-source tavily
```

Enable LLM-as-judge correctness scoring (default evaluator is F1):

```bash
  --evaluator llm --judge-model gpt-4o-mini --judge-provider openai --run-timeout-seconds 300
```

Using Gemini via Vertex AI (requirements already include `langchain-google-vertexai`; set `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_REGION` or pass flags):

```bash
python main.py \
  --provider google-vertex \
  --vertex-project $GOOGLE_CLOUD_PROJECT \
  --vertex-location us-central1 \
  --executor-model gemini-1.5-flash \
  --planner-model gemini-1.5-flash \
  --replanner-model gemini-1.5-flash \
  --questions-file data/hotpot_dev_questions.csv \
  --evidence-source tavily
```

LLM-as-judge correctness (default evaluator is F1):

```bash
  --evaluator llm --judge-model gpt-4o-mini
```

## 3. Artifact layout

```
examples/langgraph/Plan-and-Execute
├── logs/                    # JSONL event logs + metadata (gitignored)
├── metrics/                 # psutil CPU/RSS snapshots (gitignored)
├── tools/sample_hotpot_questions.py # Helper to sample HotpotQA locally
├── main.py                  # CLI wrapper around the LangGraph workflow
├── plan-and-execute.ipynb   # Original reference notebook
├── README.md                # You are here
├── requirements.txt         # Python dependencies
└── sample_output.md         # Captured run to verify expectations
```

- `logs/run_YYYYMMDD_HHMMSS.otel.jsonl` – OpenTelemetry spans that follow `otel_template/otel_span_template.json`
- `logs/run_YYYYMMDD_HHMMSS.jsonl` – ordered LangGraph node emissions
- `logs/run_YYYYMMDD_HHMMSS.metadata.json` – CLI arguments, env presence, and pointers to the event/trace artefacts
- `metrics/plan-and-execute_YYYYMMDD_HHMMSS.metrics.jsonl` – psutil CPU/RSS snapshots that match `otel_template/otel_metrics_template.json`

## 4. Integrating with other harnesses

- To batch repeated runs, use the shared driver: `python ../run_benchmarks.py --benchmark plan_and_execute --runs 3`
- Extend the executor prompt or inject additional tools by editing `build_plan_execute_app` in `main.py`
- Swap to non-OpenAI models (Gemini via Vertex AI) by setting `--provider google-vertex --vertex-project <project> [--vertex-location <region>]` and pointing `--executor-model/--planner-model/--replanner-model` to Gemini model ids.
- Enable LLM-as-judge correctness scoring with `--evaluator llm --judge-model gpt-4o-mini` (default evaluator is token F1).

This structure mirrors the other MAS traces benchmarks so the plan-and-execute agent can now participate in perf suites, regression tests, or MCP demos without firing up Jupyter.

## 5. MAS agent-outcome metrics

Plan-and-Execute shares the same agent outcome schema (retry/failure/useless) implemented across the other MAS LangGraph apps. Use this table as a quick reference when comparing traces:

| Attribute | Tree-of-Thoughts | Language Agent Tree Search | CRAG | Plan-and-Execute | Notes |
| --- | --- | --- | --- | --- | --- |
| `agent.retry.attempt_number` + friends | `[x]` | `[x]` | `[x]` | `[x]` | Planner spans don’t usually retry, but the re-planner tracks how many times it had to refresh the plan before producing a response; the previous span id is persisted in the LangGraph state. |
| `agent.failure.category` / `agent.failure.reason` | `[x]` | `[x]` | `[x]` | `[x]` | Empty plan outputs are tagged as `category=quality`; crash/timeout handling can set `system`/`timeout` consistently with the other benchmarks. |
| `agent.output.useless` (+ `_reason`) | `[x]` | `[x]` | `[x]` | `[x]` | Planner/replanner spans mark themselves useless when no steps or responses are produced; successful runs clear the flag. |

To add new nodes (extra tools, verification steps, etc.) while keeping telemetry in sync:

- Reuse the `replan_retry_attempts` + `replan_previous_span_id` fields in the LangGraph state as a template for any other loop that needs retries.
- Pass `agent_context` into `run_llm_with_span` to set the retry attributes automatically and call `set_agent_usefulness` / `set_agent_failure_attributes` inside your post-processing hook for the node.
- Document the semantics in this README whenever a node uses new `agent.*` values so other teams know how to interpret the spans.
