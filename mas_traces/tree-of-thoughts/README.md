# Tree of Thoughts Benchmark

This directory converts the `tot.ipynb` tutorial into a repeatable CLI benchmark that can be orchestrated by `mas_traces/run_benchmarks.py`. The code mirrors the notebook's Tree-of-Thoughts (ToT) search loop for the Game of 24 dataset: it expands candidate equations with an OpenAI model, scores each attempt, and prunes the search frontier until it solves the puzzle or hits a depth limit. Every execution emits human-readable traces plus structured metadata under `logs/` so you can diff runs later.

```
┌──────────────┐      ┌───────────────┐      ┌───────────┐      ┌──────────────┐
│ Puzzle CSV   │ ───▶ │ ToT expander  │ ───▶ │ Scorer    │ ───▶ │ Beam pruning │
└──────────────┘      └───────────────┘      └───────────┘      └──────────────┘
                                       │
                                       ▼
                                Run metadata + log
```

## 1. Environment setup

```bash
cd mas_traces/tree-of-thoughts
python -m venv .venv && source .venv/bin/activate  # or use uv
pip install -r requirements.txt
```

Set the required credentials before running:

- `OPENAI_API_KEY` – used by `langchain-openai` for the expander model
- (Optional) `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT` if you want LangSmith traces
- OpenTelemetry dependencies are baked into `requirements.txt`, so installing those packages will enable the on-disk trace exporter (`logs/run_<timestamp>.otel.jsonl`).

## 2. Run the benchmark

```bash
python main.py \
  --model gpt-4o-mini \
  --metrics-interval 0.5 \
  --problem-index 0 \
  --num-puzzles 1 \
  --beam-size 3 \
  --branching-factor 3 \
  --max-tokens 1024
```

Each run writes `logs/run_<timestamp>.log` (console-style step trace), `run_<timestamp>.otel.jsonl` (OpenTelemetry spans), a matching `run_<timestamp>.metadata.json` with the arguments/dataset/outcomes, and `metrics/tree-of-thoughts_<timestamp>.metrics.jsonl` (psutil CPU/RSS snapshots that follow `otel_template/otel_metrics_template.json`). You can also invoke this bench via the shared runner: `python ../run_benchmarks.py --benchmark tree_of_thoughts`.

### CLI flags

| Flag | Description |
| --- | --- |
| `--model` | OpenAI Chat Completions model used for expansions (default `gpt-4o-mini`). |
| `--temperature` | Forwarded to `ChatOpenAI` to control sampling. |
| `--problem-index` | Zero-based starting row from the dataset (default `0`). |
| `--num-puzzles` | Number of sequential puzzles to attempt. |
| `--beam-size` | How many scored candidates survive each pruning step. |
| `--branching-factor` | Number of guesses the LLM must return per expansion round (default `3`). |
| `--max-depth` | Depth cut-off for the BFS loop. |
| `--score-threshold` | Required score (1=perfect) before declaring success. |
| `--dataset-file` | Optional local CSV with a `puzzle` column. Defaults to `data/game_of_24_sample.csv`. |
| `--dataset-url` | Remote CSV URL fallback if no local dataset is provided. |
| `--max-tokens` | Upper bound on tokens returned by the LLM per expansion (default `1024`). |
| `--metrics-interval` | Seconds between psutil samples for system metrics (default `15`, override via `TOT_METRICS_INTERVAL_SECONDS`). |

## Dataset options

Three ready-to-use sources cover most testing needs:

1. `data/game_of_24_sample.csv` – the tiny 11-row file checked into the repo so smoke tests can run offline.
2. `data/game_of_24_google_full.csv` – the “real” Game of 24 dataset distributed alongside the Tree-of-Thoughts tutorial (`https://storage.googleapis.com/benchmarks-artifacts/game-of-24/24.csv`). We mirrored it locally so you can run the canonical benchmark without depending on the network. Reference: *Yao et al., Tree of Thoughts: Deliberate Problem Solving with Large Language Models (2023).*
3. `data/game_of_24_benchmark.csv` – a 50-puzzle suite generated from every solvable combination of card values (`1-13`). The script enforces balanced coverage across structural categories (all-equal, triples, two pairs, tight pairs, wide-range uniques, etc.) and attaches descriptive tags (`has_ace`, `face_card`, `prime_heavy`, ...). Run the benchmark with `--dataset-file data/game_of_24_benchmark.csv` to exercise that suite end-to-end.

If you prefer streaming directly, `--dataset-url https://storage.googleapis.com/benchmarks-artifacts/game-of-24/24.csv` still works—the flag points to the same Google artifact.

All CSVs just need a column named `puzzle` (extra metadata columns are ignored), so you can mix and match files or build your own.

## 3. Project layout

```
mas_traces/tree-of-thoughts
├── data/game_of_24_sample.csv      # Tiny offline dataset used by default
├── data/game_of_24_google_full.csv # Full ToT dataset mirrored from Google
├── data/game_of_24_benchmark.csv  # Curated 50-case suite with category/tags
├── logs/                        # Writable directory for run logs (gitignored)
├── main.py                      # ToT CLI benchmark
├── README.md                    # This guide
├── requirements.txt             # Python dependencies
└── tot.ipynb                    # Original notebook for reference
```

## 4. Converting spans to the OTEL template

Every run already emits `logs/run_<timestamp>.otel.jsonl`, but if you need to reshape those spans to match `otel_template/otel_span_template.json`, you can run the translator:

```bash
cd mas_traces/tree-of-thoughts
python scripts/translate_traces.py --source logs --dest translated_traces
```

- `--source` defaults to `logs/`.
- `--dest` defaults to `translated_traces/`.
- Pass `--overwrite` to regenerate outputs.

Each `run_<ts>.otel.jsonl` becomes `run_<ts>.otel.translated.json` (array of spans in the canonical format) without mutating the originals.

To try different puzzles, edit the CSV under `data/` or provide `--dataset-file`. The benchmark structure makes it easy to integrate future scoring strategies or alternative search heuristics while keeping the notebook implementation intact for educational purposes.
