# Parquet plotting helpers (Paper stats helpers)

Scripts in this folder reproduce the paper figures from the consolidated parquet dataset under `data/parquet/`.
The canonical entrypoints now live alongside the other plot scripts in `plot/`; files in this folder are
compatibility wrappers so existing commands keep working.
Shared parquet utilities live in `plot/lib` so these scripts align with the rest of the plotting stack.

Execution location: run all plotting commands from `mas-benchmark/plot`.
Paths below are relative to that directory. Install dependencies from `requirements.txt` into your existing venv.

## Data download

Figures in our paper can be reproduced using our public dataset on HuggingFace, which
is available at: https://huggingface.co/datasets/kaust-generative-ai/maestro-mas-benchmark.

Before running any plotting scripts, in this directory, run:

```bash
pip install -U "huggingface_hub[cli]"
hf download kaust-generative-ai/maestro-mas-benchmark --repo-type dataset --local-dir ../data --include "*.parquet"
```

This downloads our dataset under `../data` directory, which has the following structure:

```bash
maestro/
└── data/
    ├── traces.parquet
    └── metrics.parquet
```

## Config format

Configs (`runs_*_parquet.json`) live under `plot/configs/` and are JSON lists or objects with `inputs`.
Each entry supports:

- `label`: series label (arch or model, depending on the plot)
- `group`: optional group label for higher-level buckets
- `example_name`: dataset example folder name (e.g., `crag`, `language-agent-tree-search`, `plan-and-execute`)
- `model`: expected model name (filters runs by model attribute)
- `variant`: `with-tavily` or `without-tavily` (maps to `tags_all: ["suite-2", "with-tavily"|"without-tavily"]`)
- `tags_all` / `tags_any`: explicit tag filters, if needed
- `color`: optional color override

The config object can also include `parquet_path` (path to `traces.parquet`) or `parquet_root`
(path to the folder containing `traces.parquet`). Defaults to the flat layout
`<repo>/parquet/traces.parquet`, then falls back to `<repo>/data/parquet/traces.parquet`.
You can also set `PARQUET_ROOT=/path/to/parquet` as an environment override.

## Figures

### Finding 1 (Figure 4 / 5)

```bash
python plot_cpu_mem_by_example_summary.py
python plot_comm_with_without_tavily.py
```

### Finding 2 (Figure 6)

```bash
python plot_cpu_mem_by_example_row.py
```

### Finding 3 (Figure 7)

```bash
python plot_avg_pairwise_call_graph_similarity.py
```

### Finding 4 (Figure 8)

```bash
python plot_model_similarity_heatmaps.py
```

### Finding 5 (Figure 9.a / 9.b)

```bash
source .venv/bin/activate
python plot_telemetry_compare.py \
  --config configs/runs_wi_by_model_parquet.json \
  --plot-output ../analysis/telemetry/plots/tradeoff_scorecard_wi_by_model_overview.pdf \
  --plot-mode overview \
  --skip-crowded-labels --label-distance-threshold 0.8 \
  --accuracy-shape-buckets

python plot_telemetry_arch_combo.py \
  --config configs/runs_wi_by_arch_parquet.json \
  --output ../analysis/telemetry/plots/consistency_accuracy_combo_wi.pdf \
  --title "Cost/Duration/Accuracy, by Arch."

deactivate
```

### Finding 6 (Figure 10)

```bash
source .venv/bin/activate
python plot_telemetry_model_boxplots.py \
  --config configs/runs_wi_by_model_parquet.json \
  --output ../analysis/telemetry/plots/model_boxplots_wi.pdf \
  --hide-accuracy-labels --hide-subplot-titles

deactivate
```

### Finding 8/9 (Figure 12.a / 12.b)

```bash
source .venv/bin/activate
python plot_telemetry_tavily_diff.py \
  --with-config configs/runs_wi_by_arch_parquet.json \
  --without-config configs/runs_wo_by_arch_parquet.json \
  --scatter-output ../analysis/telemetry/plots/tavily_shift_latency_cost.pdf \
  --skip-crowded-labels --label-distance-threshold 0.8 \
  --relative-percent --color-mode model \
  --shade-arch-background --arch-boundary-color '#607B8F' --arch-background-alpha 0.27 \
  --no-arch-boundary --no-title

python plot_telemetry_tavily_diff.py \
  --with-config configs/runs_wi_by_arch_parquet.json \
  --without-config configs/runs_wo_by_arch_parquet.json \
  --scatter-output ../analysis/telemetry/plots/tavily_shift_latency_cost.pdf \
  --facet-output ../analysis/telemetry/plots/tavily_shift_latency_cost_facet.pdf \
  --accuracy-output ../analysis/telemetry/plots/tavily_accuracy_delta.pdf \
  --arrow-distance-threshold 0.08 \
  --skip-crowded-labels --label-distance-threshold 0.8 \
  --relative-percent --color-mode model \
  --shade-arch-background --arch-boundary-color '#607B8F' --arch-background-alpha 0.27 \
  --title "Impact of Web Search (without → with)" --no-arch-boundary

deactivate
```

## Notes

- These scripts only scan parquet rows that match the `example_name` filters in the configs.
- Tavily configs rely on `suite-2` tags from `convert_dataset_to_parquet.py` to separate with/without runs.
