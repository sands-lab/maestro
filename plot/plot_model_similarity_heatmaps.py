#!/usr/bin/env python3
"""Plot model-to-model call graph similarity heatmaps for with/without Tavily."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.comparison import compute_lcs_similarity
from .lib.data_loaders import load_traces
from .lib.extractors import extract_call_sequence


DEFAULT_EXAMPLES = (
    "crag",
    "language-agent-tree-search",
    "plan-and-execute",
)

EXAMPLE_DISPLAY_NAMES = {
    "crag": "CRAG",
    "language-agent-tree-search": "LATS",
    "plan-and-execute": "Plan-and-Execute",
}

DEFAULT_METRICS = ("lcs",)

MODEL_ATTR_KEYS = (
    "gen_ai.request.model",
    "gen_ai.response.model",
    "model.name",
)

TRACE_COLUMNS = (
    "span_id",
    "parent_span_id",
    "name",
    "agent_name",
    "attributes",
    "resource",
    "start_time",
    "run_id",
)


def _format_model_label(model: str) -> str:
    tokens = model.split("-")
    if len(tokens) < 2:
        return model
    vendor_map = {"gemini": "Ge", "gpt": "G"}
    suffix_map = {"flash": "F", "lite": "L", "mini": "M", "nano": "N"}
    vendor = vendor_map.get(tokens[0], tokens[0][:2].title())
    version = tokens[1].replace(".", "")
    suffixes = [suffix_map.get(tok, tok[:1].upper()) for tok in tokens[2:]]
    return f"{vendor}{version}{''.join(suffixes)}"


def _format_example_label(example: str) -> str:
    return EXAMPLE_DISPLAY_NAMES.get(example, example)


def _resolve_path(path: str | Path, project_root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (project_root / candidate).resolve()


def _iter_model_dirs(spans_dir: Path) -> List[Path]:
    if not spans_dir.exists():
        return []
    return sorted(
        path
        for path in spans_dir.iterdir()
        if path.is_dir() and path.name != "v1"
    )


def _get_tavily_dir(model_dir: Path, setting: str) -> Path | None:
    candidate = model_dir / setting
    return candidate if candidate.is_dir() else None


def _is_embedding_model(model: str) -> bool:
    return "embedding" in model


def _should_skip_model(model: str) -> bool:
    return model == "unknown" or _is_embedding_model(model)


def _extract_model_name(span: Dict) -> str:
    attrs = span.get("attributes", {}) or {}
    for key in MODEL_ATTR_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    resource = span.get("resource", {}) or {}
    resource_attrs = resource.get("attributes", {}) if isinstance(resource, dict) else {}
    value = resource_attrs.get("model.name")
    if isinstance(value, str) and value.strip():
        return value
    return "unknown"


def _infer_run_model(spans: List[Dict]) -> str:
    counts: Counter[str] = Counter()
    for span in spans:
        model_name = _extract_model_name(span)
        if _should_skip_model(model_name):
            continue
        counts[model_name] += 1
    if counts:
        return counts.most_common(1)[0][0]
    for span in spans:
        model_name = _extract_model_name(span)
        if model_name != "unknown":
            return model_name
    return "unknown"


def _collect_model_runs(spans_dir: Path, setting: str) -> Dict[str, Dict[str, List]]:
    model_runs: Dict[str, Dict[str, List]] = {}
    for model_dir in _iter_model_dirs(spans_dir):
        tavily_dir = _get_tavily_dir(model_dir, setting)
        if not tavily_dir:
            continue
        trace_files = sorted(tavily_dir.rglob("*.otel.jsonl"))
        if not trace_files:
            continue
        sequences = []
        graphs = []
        for trace_file in trace_files:
            traces = load_traces(str(trace_file))
            sequence = extract_call_sequence(traces)
            sequences.append(sequence)
            graphs.append(Counter(sequence))
        model_runs[model_dir.name] = {
            "sequences": sequences,
            "graphs": graphs,
        }
    return model_runs


def _collect_model_runs_parquet(
    traces_file: Path,
    example: str,
    setting: str,
) -> Dict[str, Dict[str, List]]:
    traces = load_traces(
        str(traces_file),
        example_name=example,
        tags=[setting],
        columns=TRACE_COLUMNS,
    )
    spans_by_run: Dict[str, List[Dict]] = defaultdict(list)
    for span in traces:
        run_id = span.get("run_id") or ""
        spans_by_run[run_id].append(span)

    model_runs: Dict[str, Dict[str, List]] = {}
    for run_id, spans in spans_by_run.items():
        model_name = _infer_run_model(spans)
        if _should_skip_model(model_name):
            continue
        sequence = extract_call_sequence(spans)
        graphs = Counter(sequence)
        entry = model_runs.setdefault(model_name, {"sequences": [], "graphs": []})
        entry["sequences"].append(sequence)
        entry["graphs"].append(graphs)
    return model_runs


def _pairwise_similarity(
    runs_a: Dict[str, List],
    runs_b: Dict[str, List],
    metric: str,
    *,
    include_self: bool = True,
) -> List[float]:
    sims: List[float] = []
    if runs_a is runs_b and not include_self:
        n = len(runs_a["sequences"])
        if n < 2:
            return sims
        if metric == "lcs":
            for i in range(n):
                for j in range(i + 1, n):
                    sims.append(
                        compute_lcs_similarity(
                            runs_a["sequences"][i],
                            runs_a["sequences"][j],
                        )
                    )
            return sims

        for i in range(n):
            edges_i = set(runs_a["graphs"][i].keys())
            for j in range(i + 1, n):
                edges_j = set(runs_a["graphs"][j].keys())
                union = edges_i | edges_j
                inter = edges_i & edges_j
                sims.append(len(inter) / len(union) if union else 0.0)
        return sims

    if metric == "lcs":
        for seq_a in runs_a["sequences"]:
            for seq_b in runs_b["sequences"]:
                sims.append(compute_lcs_similarity(seq_a, seq_b))
        return sims

    for graph_a in runs_a["graphs"]:
        edges_a = set(graph_a.keys())
        for graph_b in runs_b["graphs"]:
            edges_b = set(graph_b.keys())
            union = edges_a | edges_b
            inter = edges_a & edges_b
            sims.append(len(inter) / len(union) if union else 0.0)
    return sims


def _aggregate(values: List[float], mode: str) -> float:
    if not values:
        return 0.0
    if mode == "median":
        return float(np.median(values))
    return float(np.mean(values))


def _build_similarity_matrix(
    model_runs: Dict[str, Dict[str, List]],
    models: List[str],
    metric: str,
    agg: str,
) -> Tuple[List[str], np.ndarray, List[bool]]:
    n = len(models)
    matrix = np.zeros((n, n), dtype=float)
    diag_singleton = [False] * n
    for i, model_i in enumerate(models):
        runs_i = model_runs[model_i]
        if len(runs_i["sequences"]) < 2:
            matrix[i, i] = np.nan
            diag_singleton[i] = True
        else:
            sims = _pairwise_similarity(runs_i, runs_i, metric, include_self=False)
            matrix[i, i] = _aggregate(sims, agg)
        for j in range(i + 1, n):
            model_j = models[j]
            sims = _pairwise_similarity(model_runs[model_i], model_runs[model_j], metric)
            score = _aggregate(sims, agg)
            matrix[i, j] = score
            matrix[j, i] = score
    return models, matrix, diag_singleton


def _plot_example_panel(
    fig,
    gs,
    row: int,
    col: int,
    example: str,
    models: List[str],
    sim_matrix: np.ndarray,
    diag_singleton: List[bool],
    metric: str,
    agg: str,
    show_ylabels: bool,
):
    ax_heat = fig.add_subplot(gs[row, col])

    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="#d3d3d3")
    heat = ax_heat.imshow(sim_matrix, vmin=0.0, vmax=1.0, cmap=cmap)
    labels = [_format_model_label(model) for model in models]
    ax_heat.set_xticks(range(len(models)))
    ax_heat.set_yticks(range(len(models)))
    ax_heat.set_xticklabels(
        labels,
        rotation=45,
        ha="right",
        rotation_mode="anchor",
        fontsize=15,
    )
    if show_ylabels:
        ax_heat.set_yticklabels(labels, fontsize=15)
    else:
        ax_heat.set_yticklabels([])
    ax_heat.grid(False)
    ax_heat.set_xticks(np.arange(-0.5, len(models), 1), minor=True)
    ax_heat.set_yticks(np.arange(-0.5, len(models), 1), minor=True)
    ax_heat.grid(which="minor", color="white", linewidth=1)
    ax_heat.tick_params(which="minor", bottom=False, left=False)
    ax_heat.tick_params(which="major", length=0)
    ax_heat.set_title(
        _format_example_label(example),
        fontsize=15,
        fontweight="bold",
    )
    # ax_heat.set_xlabel("Model", fontsize=15)
    # ax_heat.set_ylabel("Model" if show_ylabels else "", fontsize=15)
    for idx, is_single in enumerate(diag_singleton):
        if not is_single:
            continue
        ax_heat.text(
            idx,
            idx,
            "n=1",
            ha="center",
            va="center",
            fontsize=8,
            color="black",
            fontweight="bold",
        )
    return heat


def _plot_similarity_figure(
    example_data: List[Tuple[str, Dict[str, Tuple[List[str], np.ndarray, List[bool]]]]],
    metrics: List[str],
    aggregate: str,
    output_file: Path,
) -> None:
    if not example_data:
        raise SystemExit("No example data found.")

    fig = plt.figure(figsize=(4.2 * len(example_data), 3.2 * len(metrics)))
    gs = fig.add_gridspec(len(metrics), len(example_data), hspace=0.01, wspace=0.01)

    heat = None
    for col, (example, matrices) in enumerate(example_data):
        for row, metric in enumerate(metrics):
            models, sim_matrix, diag_singleton = matrices[metric]
            heat = _plot_example_panel(
                fig,
                gs,
                row,
                col,
                example,
                models,
                sim_matrix,
                diag_singleton,
                metric,
                aggregate,
                show_ylabels=(col == 0),
            )

    if heat is not None:
        cbar = fig.colorbar(heat, ax=fig.axes, shrink=0.75, pad=0.015)
        cbar.set_label(f"{aggregate.capitalize()} similarity", fontsize=12)

    fig.tight_layout(pad=0.5)
    fig.savefig(str(output_file), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot to: {output_file}")


def _print_panel_means(
    setting: str,
    example_data: List[Tuple[str, Dict[str, Tuple[List[str], np.ndarray, List[bool]]]]],
    metrics: List[str],
) -> None:
    print(f"\nPanel means ({setting}):")
    for example, matrices in example_data:
        for metric in metrics:
            _, sim_matrix, _ = matrices[metric]
            values = sim_matrix[~np.isnan(sim_matrix)]
            mean_val = float(np.mean(values)) if values.size else float("nan")
            print(f"[{setting}] {example} {metric} mean={mean_val:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot model similarity heatmaps for with/without Tavily runs.",
    )
    parser.add_argument(
        "--traces-file",
        type=str,
        default="data/traces.parquet",
        help="Parquet traces file (preferred).",
    )
    parser.add_argument(
        "--root-dir",
        type=str,
        default="yxc-251224",
        help="Legacy root directory containing example subfolders (JSONL layout).",
    )
    parser.add_argument(
        "--examples",
        type=str,
        default=",".join(DEFAULT_EXAMPLES),
        help="Comma-separated example names to include.",
    )
    parser.add_argument(
        "--metrics",
        type=str,
        default=",".join(DEFAULT_METRICS),
        help="Comma-separated metrics to plot (lcs,jaccard).",
    )
    parser.add_argument(
        "--aggregate",
        type=str,
        choices=("mean", "median"),
        default="median",
        help="Aggregation function for pairwise similarities.",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="plot/figures/comparison/similarity/model_similarity_heatmap.pdf",
        help="Output PDF path.",
    )
    args = parser.parse_args()

    traces_file = _resolve_path(args.traces_file, _project_root)
    root_dir = _resolve_path(args.root_dir, _project_root)
    output_file = _resolve_path(args.output_file, _project_root)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    examples = [name.strip() for name in args.examples.split(",") if name.strip()]
    if not examples:
        raise SystemExit("No examples provided.")

    metrics = [name.strip().lower() for name in args.metrics.split(",") if name.strip()]
    if not metrics:
        raise SystemExit("No metrics provided.")
    for metric in metrics:
        if metric not in DEFAULT_METRICS:
            raise SystemExit(f"Unsupported metric: {metric}")

    suffix = output_file.suffix or ".pdf"
    stem = output_file.with_suffix("")

    use_parquet = traces_file.suffix.lower() == ".parquet" and traces_file.exists()
    for setting in ("with-tavily",):
        example_data = []
        for example in examples:
            if use_parquet:
                model_runs = _collect_model_runs_parquet(traces_file, example, setting)
            else:
                spans_dir = root_dir / example / "spans"
                if not spans_dir.exists():
                    print(f"Skipping {example}: spans dir not found at {spans_dir}")
                    continue
                model_runs = _collect_model_runs(spans_dir, setting)
            if not model_runs:
                print(f"Skipping {example}: no {setting} runs found")
                continue
            models = sorted(model_runs.keys())
            matrices: Dict[str, Tuple[List[str], np.ndarray, List[bool]]] = {}
            for metric in metrics:
                _models, sim_matrix, diag_singleton = _build_similarity_matrix(
                    model_runs,
                    models,
                    metric,
                    args.aggregate,
                )
                matrices[metric] = (_models, sim_matrix, diag_singleton)
            example_data.append((example, matrices))

        if not example_data:
            print(f"No example data found for {setting}.")
            continue

        output_path = Path(f"{stem}_{setting}{suffix}")
        _plot_similarity_figure(example_data, metrics, args.aggregate, output_path)
        _print_panel_means(setting, example_data, metrics)


if __name__ == "__main__":
    main()
