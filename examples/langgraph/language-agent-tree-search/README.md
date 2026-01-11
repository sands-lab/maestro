# Language Agent Tree Search Benchmark

This directory provides a CLI benchmark for Language Agent Tree Search (LATS), a general LLM agent search algorithm by Zhou, et. al. LATS combines reflection/evaluation and Monte Carlo Tree Search to achieve better task performance compared to techniques like ReACT, Reflexion, or Tree of Thoughts. The benchmark allows you to test the LATS workflow—initial candidate generation, Monte Carlo-style expansion, Tavily tool calls, self-reflection, and loop termination—against a list of questions while capturing reproducible logs and metadata.

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
cd examples/langgraph/language-agent-tree-search
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
  --questions-file data/questions.csv \
  --evidence-source tavily

Using Gemini via Vertex AI:

```bash
python main.py \
  --provider google-vertex \
  --vertex-project $GOOGLE_CLOUD_PROJECT \
  --vertex-location us-central1 \
  --model gemini-1.5-flash \
  --questions-file data/hotpot_dev_questions.csv \
  --evidence-source tavily
```

Enable LLM-as-judge correctness scoring (default evaluator is F1):

```bash
  --evaluator llm --judge-model gpt-4o-mini --judge-provider openai --run-timeout-seconds 300
```

Batch runs: use the helper script from repo root (call with `bash` if not executable):

```bash
bash tools/run_lats_batch.sh --start 0 --end 9
```

Add extra args after `--`, e.g. different model:

```bash
bash tools/run_lats_batch.sh --start 0 --end 9 -- --model gemini-1.5-flash --run-timeout-seconds 300
```

Each run now writes:

- `logs/run_<timestamp>.log` – human-readable stream of graph events
- `logs/run_<timestamp>.metadata.json` – CLI args, dataset provenance, solved stats, and artifact pointers
- `logs/run_<timestamp>.otel.jsonl` – OpenTelemetry spans following `otel_template/otel_span_template.json`
- `metrics/language-agent-tree-search_<timestamp>.metrics.jsonl` – psutil-derived CPU/RSS snapshots that match `otel_template/otel_metrics_template.json`

The span + metrics files are produced via the shared `plugin_monitoring.langgraph_otel` helpers so they can be reused across other LangGraph examples. You can also invoke this benchmark via the shared runner: `python ../run_benchmarks.py --benchmark language_agent_tree_search`.
Each span includes accumulated `gen_ai.usage.*` counters and message byte sizes captured through LangChain callbacks.
We also tag each span’s `gen_ai.operation.name` so dashboards can distinguish between `call_llm`, `execute_tool`, and `invoke_agent` operations outlined in `otel_span_template.json`.

### OTEL instrumentation quick reference

- `plugin_monitoring/langgraph_otel.py` exports `run_llm_with_span` and `run_tool_with_span`; always wrap LangChain LLM calls with the former and Tavily/MCP/tool payloads with the latter so `gen_ai.operation.name` is correctly set to `call_llm` or `execute_tool`.
- The helpers automatically attach a `LangChainUsageCallback`, aggregate token counts, and populate `communication.input/output/total_message_size_bytes` so you never have to wire those attributes manually in `main.py`.
- Use `invoke_agent_span` for question/node orchestration spans (`gen_ai.operation.name=invoke_agent`) and call `record_invoke_agent_output(span, result, input_bytes)` once the agent produces an answer; both helpers live in `plugin_monitoring.langgraph_otel` so other LangGraph apps can stay minimal.
- For bespoke steps that don’t fit the helpers, fall back to `estimate_message_bytes(value)` to keep `communication.*` metrics aligned with the template.
- When you add a brand new operation, document whether it maps to `call_llm`, `execute_tool`, or `invoke_agent` and ensure the payload that hits the external system (LLM prompt, MCP request, tool args) is what you feed into the message-size helper; downstream analysis relies on these values being comparable across applications.

### MAS agent-outcome metrics

All MAS LangGraph benchmarks now emit the same retry/failure/useless attributes so dashboards can slice behavior across scenarios. Use the table below as a quick comparison reference (the checkboxes indicate which benchmarks currently populate each attribute group):

| Attribute | Tree-of-Thoughts | Language Agent Tree Search | CRAG | Plan-and-Execute | Notes |
| --- | --- | --- | --- | --- | --- |
| `agent.retry.attempt_number` + friends | `[x]` | `[x]` | `[x]` | `[x]` | LATS increments the counter when an expansion round produced no viable candidates; the span id from the failed attempt is stored in the LangGraph state so retried spans point back to the previous attempt. |
| `agent.failure.category` / `agent.failure.reason` | `[x]` | `[x]` | `[x]` | `[x]` | We tag expansions with `category=quality` when the generations array is empty. CRAG/Plan tie the same fields to their remediation logic. |
| `agent.output.useless` (+ `_reason`) | `[x]` | `[x]` | `[x]` | `[x]` | Each LATS expansion span marks itself as useless when LangChain returns no candidates; other benchmarks flag useless Tavily/tool/planner calls similarly. |

Implementation notes for new nodes:

- Keep `expand_retry_count` and `expand_previous_span_id` on the LangGraph state so retries automatically inherit context; this pattern works for other loops too.
- Pass the state-derived `AgentCallContext` into `run_llm_with_span(..., agent_context=...)` so the helper sets the OTEL attributes without boilerplate.
- Post-processors such as `_annotate_expand` should call `set_agent_failure_attributes` / `set_agent_usefulness` (exported from `plugin_monitoring.langgraph_otel`) instead of writing OTEL keys by hand—future use cases can copy the same pattern.

### CLI flags

| Flag | Description |
| --- | --- |
| `--model` | Chat Completions model passed to `ChatOpenAI` (default `gpt-4o-mini`). |
| `--provider` | `openai` (default) or `google-vertex` to use Gemini via Vertex AI. |
| `--vertex-project` / `--vertex-location` | Project/region for Vertex AI (required when `--provider google-vertex`; defaults to env vars). |
| `--temperature` | Forwarded to both the initial candidate and expansion chains. |
| `--max-depth` | Maximum tree height before the search stops (default `5`). |
| `--branching-factor` | Number of candidate continuations sampled per expansion round (default `5`). |
| `--questions-file` | CSV with a `question` column (HotpotQA sampler outputs this). Defaults to `data/questions.csv`. |
| `--evidence-source` | `tavily` (default) uses Tavily search results; `dataset` injects reference passages from the CSV and disables Tavily calls. |
| `--evaluator` | `f1` (default) or `llm` to grade answers with an LLM judge. |
| `--judge-model` | Model for the LLM judge when `--evaluator llm` (e.g., `gpt-4o-mini`). |
| `--start-index` | 0-based index in the dataset to start from (default `0`). |
| `--num-questions` | Number of questions to run sequentially (default `1`). |
| `--tavily-max-results` | Cap the Tavily search tool output (default `5`). |
| `--metrics-interval` | Seconds between psutil samples for system metrics (default `15`, override via `LATS_METRICS_INTERVAL_SECONDS`). |

### Sampling HotpotQA locally

Use the built-in helper to turn the official JSON dumps into CSVs with gold paragraphs plus distractors so you can benchmark with or without external search:

```bash
cd examples/langgraph/language-agent-tree-search
python tools/sample_hotpot_questions.py \
  --source data/hotpot_dev_distractor_v1.json \
  --dest data/hotpot_dev_questions.csv \
  --sample-size 200
```

Once the CSV exists you can run the agent using the dataset-provided context instead of Tavily by passing `--evidence-source dataset`, for example:

```bash
python main.py \
  --questions-file data/hotpot_dev_questions.csv \
  --metrics-interval 0.5 \
  --evidence-source dataset \
  --num-questions 5
```

## 3. Project layout

```
examples/langgraph/language-agent-tree-search
├── data/questions.csv          # Simple default dataset extracted from the notebook
├── logs/                       # Run logs + metadata (gitignored, .gitkeep placeholder)
├── metrics/                    # System metrics snapshots (gitignored)
├── main.py                     # CLI benchmark implementation
├── README.md                   # This guide
├── requirements.txt            # Python dependencies
├── tools/sample_hotpot_questions.py # HotpotQA sampling helper for datasets
└── lats.ipynb                  # Original notebook for reference
```

The benchmark stays close to the notebook logic so you can tweak prompts, branching factors, or reward shaping while still producing reproducible artifacts for downstream automation and regression testing.

## Telemetry visualization helpers

From repo root (`cd tools && source .venv/bin/activate`), the shared scripts can summarize OTEL traces and visualize Tavily on/off impact:

- **Architecture/model scorecards** (tokens/latency/cost/accuracy medians) with de-cluttered labels:

  ```bash
  python telemetry_compare.py \
    --config runs_wi_by_model.json \
    --plot-output ../analysis/telemetry/plots/tradeoff_scorecard_wi_by_model_overview.pdf \
    --accuracy-arch-plot-output ../analysis/telemetry/plots/accuracy_by_arch_wi.pdf \
    --skip-crowded-labels --label-distance-threshold 0.18
  ```

- **invoke_agent call volume vs accuracy** (per model; labels auto-hide when crowded):

  ```bash
  python telemetry_depth_accuracy.py \
    --config runs_wi_by_arch.json \
    --scatter-output ../analysis/telemetry/plots/depth_vs_accuracy_wi.pdf \
    --label-distance-threshold 0.12 \
    --title "invoke_agent calls vs accuracy (with Tavily)"
  ```

- **Web-search impact** (without → with Tavily) using arrowed shifts for big moves, thin lines for small moves, and optional label suppression:

  ```bash
  python telemetry_tavily_diff.py \
    --with-config runs_wi_by_arch.json \
    --without-config runs_wo_by_arch.json \
    --scatter-output ../analysis/telemetry/plots/tavily_shift_latency_cost.pdf \
    --accuracy-output ../analysis/telemetry/plots/tavily_accuracy_delta.pdf \
    --arrow-distance-threshold 0.08 \
    --skip-crowded-labels --label-distance-threshold 0.12
  ```

Outputs are saved under `analysis/telemetry/plots/`; tune the thresholds to control how many arrows/labels appear.
