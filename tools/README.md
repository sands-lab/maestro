# Tools

Utility scripts that support the MAS benchmarks live in this directory. Each
script is intended to be executed from within `tools/` so relative paths (e.g.
`../examples/...`) resolve correctly.

## Setup

Create a dedicated virtual environment for the tools so plotting dependencies do
not interfere with the benchmark environments:

```bash
cd tools
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

All sample commands below assume the virtualenv is activated and you are sitting
in the `tools/` directory. Visualization artifacts should be written to
`../analysis/telemetry`, which contains a README describing the recommended
layout (`plots/`, `summary/`, etc.); the script will create subdirectories as
needed.

## OTEL usage summary (`otel_usage_summary.py`)

Aggregates token usage and span durations from the LangGraph OTEL JSONL logs.
The script understands both single files and directories (every `*.otel.jsonl`
under a directory is processed). External operations (`call_llm`,
`execute_tool`, `invoke_agent`) are included by default, but you can specify a
custom list via `--operations`.

### Example: single log sanity check

```bash
python otel_usage_summary.py \
  --logs ../examples/langgraph/tree-of-thoughts/logs/run_20251218_104549.otel.jsonl \
  --summary-table \
  --plot-output ../analysis/telemetry/plots/run_20251218_104549_bars.pdf
```

### Example: multiple runs via config file

Create a config file under `tools/`:

```json
{
  "inputs": [
    {"label": "tot", "path": "../examples/langgraph/tree-of-thoughts/logs", "color": "#4e79a7"},
    {"label": "crag", "path": "../examples/langgraph/crag/logs", "color": "#f28e2b"}
  ]
}
```

Then summarize and plot:

```bash
python otel_usage_summary.py \
  --config runs.json \
  --summary-table \
  --label-table \
  --ratio-table \
  --plot-output ../analysis/telemetry/plots/run_bars.pdf \
  --label-plot-output ../analysis/telemetry/plots/label_box.pdf \
  --per-step-csv ../analysis/telemetry/summary/steps.csv
```

This produces:

- Per-run summary table (or CSV via `--summary-csv`)
- Per-label aggregated table if `--label-table` is provided
- Optional bar plot and box plots when `--plot-output`/`--label-plot-output`
  are set and `matplotlib` is installed
- Optional per-step CSV for deeper inspection

### Arguments reference

Run `python otel_usage_summary.py --help` for the full list. Key flags:

- `--logs`: one or more log files/directories.
- `--config`: JSON file specifying `{"inputs": [{"label": "...", "path": "..."}]}`.
- `--summary-csv`, `--per-step-csv`: write CSV outputs (store them under
  `../analysis/telemetry/tables` for consistency).
- `--summary-table`, `--label-table`, `--per-step-table`, `--ratio-table`: print
  formatted tables only when requested and automatically persist their CSV
  versions under `../analysis/telemetry/tables`.
- `--ratio-table`, `--baseline-label`: compare average tokens/durations against
  a baseline label (defaults to the first entry in the config/log list).
- `--pricing-file`: optional JSON map of per-model token prices (auto-loads
  `tools/pricing.json` if present). Keys should match `gen_ai.system` and model
  from the traces, e.g. `vertex_ai/gemini-2.5-flash` or `openai/gpt-5o-mini`.
  Values can be `{ "input_per_1m": <num>, "output_per_1m": <num> }` or a single
  number applied to both. See `tools/pricing.json` for an example.
- `--price-per-1m`: fallback flat price per 1M tokens if no pricing entry
  matches; leave unset to omit costs.
- `--plot-output`, `--label-plot-output`: save visualizations; outputs are saved
  as PDF regardless of the extension you pass.
- `--baseline-plot-output`: save a per-task baseline comparison plot (tokens,
  duration, cost, failure rate) using the first label or `--baseline-label`.
  Colors honor the optional `color` fields from the config.
- `--latency-plot-output`: grouped box plot of duration per task (grouping follows
  the config structure).
- `--cost-plot-output`: grouped box plot of cost per task (PDF only, honors config
  colors and grouping).
- `--accuracy-plot-output`: grouped box plot of accuracy (PDF only, honors config
  colors and grouping).
- `--operations`: filter spans by `gen_ai.operation.name`.

### Customizing further

The script is now a thin CLI wrapper around two modules:

- `otel_data.py` handles reading OTEL logs, aggregating metrics, pricing, and CSV/tables.
- `otel_plots.py` contains plotting helpers (PDF output).

Import these modules directly from Python if you want bespoke plots (e.g., highlight a
single series) without adding general-purpose flags to the CLI.

Feel free to extend `tools/` with additional scripts following the same
documentation pattern so future users know how to run them.

### Pricing references

- OpenAI model pricing: https://platform.openai.com/docs/pricing
- Google Gemini pricing (public/Vertex): https://ai.google.dev/gemini-api/docs/pricing

## Paper stats helpers (Findings X)

Scripts in this section reproduce the statistics reported in the paper.

### How do different agent architectures affect task performance and stability? (Finding 5)

Summarize cost/duration/accuracy per architecture across all models.

```bash
# Figure 9.a
source .venv/bin/activate
python telemetry_compare.py   --config runs_wi_by_model.json   --plot-output ../analysis/telemetry/plots/tradeoff_scorecard_wi_by_model_overview.pdf   --plot-mode overview   --skip-crowded-labels --label-distance-threshold 0.8   --accuracy-shape-buckets
# Figure 9.b
python telemetry_arch_combo.py --config runs_wi_by_arch.json --output ../analysis/telemetry/plots/consistency_accuracy_combo_wi.pdf --title "Cost/Duration/Accuracy, by Architecture"
deactivate
```

### How does model choice affect MAS behavior? (Finding 6)

```bash
# Figure 10
source .venv/bin/activate
python telemetry_model_boxplots.py --config runs_wi_by_model.json --output ../analysis/telemetry/plots/model_boxplots_wi.pdf --hide-accuracy-labels --hide-subplot-titles
deactivate
```

### What are the dominant failure modes in LLM-based multi-agent systems? (Finding 7)

Plot failure composition and model-by-category heatmap from the attribution TSVs.

```bash
# Figure 11.a
source .venv/bin/activate
python telemetry_failure_plots.py   --category-tsv ../analysis/telemetry/failure_stats/failure_by_category.tsv   --model-tsv ../analysis/telemetry/failure_stats/failure_by_model_category.tsv   --pie-output ../analysis/telemetry/plots/failure_pie.pdf   --heatmap-output ../analysis/telemetry/plots/failure_heatmap.pdf --hide-heatmap-labels
# Figure 11.b
python telemetry_judge_heatmap_facet.py   --input-dirs     ../analysis/telemetry/judge/gemini-2.5-flash/with_tavily     ../analysis/telemetry/judge/gpt-4o/with_tavily     ../analysis/telemetry/judge/openai/gpt-oss-120b/with_tavily   --titles "judge: gemini-2.5-flash" "judge: gpt-4o" "judge: gpt-oss-120b"   --output ../analysis/telemetry/plots/judge_heatmap_facet.pdf --hide-heatmap-labels
deactivate
```

### How does tool usage impact cost and accuracy? (Finding 8, 9)

Compute median accuracy/cost/latency deltas and \% of runs with positive accuracy gains
when enabling Tavily.

```bash
# Figure 12.a
source .venv/bin/activate
python telemetry_tavily_diff.py --with-config runs_wi_by_arch.json --without-config runs_wo_by_arch.json --scatter-output ../analysis/telemetry/plots/tavily_shift_latency_cost.pdf --skip-crowded-labels --label-distance-threshold 0.8 --relative-percent --color-mode model --shade-arch-background --arch-boundary-color '#607B8F' --arch-background-alpha 0.27 --no-arch-boundary --no-title
# Figure 12.b
python telemetry_tavily_diff.py --with-config runs_wi_by_arch.json --without-config runs_wo_by_arch.json --scatter-output ../analysis/telemetry/plots/tavily_shift_latency_cost.pdf --facet-output ../analysis/telemetry/plots/tavily_shift_latency_cost_facet.pdf --accuracy-output ../analysis/telemetry/plots/tavily_accuracy_delta.pdf --arrow-distance-threshold 0.08 --skip-crowded-labels --label-distance-threshold 0.8 --relative-percent --color-mode model --shade-arch-background --arch-boundary-color '#607B8F' --arch-background-alpha 0.27 --title "Impact of Web Search (without → with)" --no-arch-boundary
deactivate
```
