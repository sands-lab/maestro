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

## 2. Run the benchmark

```bash
python main.py \
  --model gpt-4o-mini \
  --problem-index 0 \
  --num-puzzles 1 \
  --beam-size 3 \
  --branching-factor 3 \
  --max-tokens 1024
```

Each run writes `logs/run_<timestamp>.log` (console-style step trace) plus a matching `run_<timestamp>.metadata.json` with the arguments, dataset source, and puzzle-level outcomes. You can also invoke this bench via the shared runner: `python ../run_benchmarks.py --benchmark tree_of_thoughts`.

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

## 3. Project layout

```
mas_traces/tree-of-thoughts
├── data/game_of_24_sample.csv   # Tiny offline dataset used by default
├── logs/                        # Writable directory for run logs (gitignored)
├── main.py                      # ToT CLI benchmark
├── README.md                    # This guide
├── requirements.txt             # Python dependencies
└── tot.ipynb                    # Original notebook for reference
```

To try different puzzles, edit the CSV under `data/` or provide `--dataset-file`. The benchmark structure makes it easy to integrate future scoring strategies or alternative search heuristics while keeping the notebook implementation intact for educational purposes.
