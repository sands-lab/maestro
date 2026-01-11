#!/usr/bin/env python3
"""Plot average pairwise call graph similarity (Jaccard + LCS) as boxplots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Dict, List

import matplotlib.pyplot as plt

_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.comparison import MultiExampleCollector


def _apply_paper_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 14,
            "axes.titlesize": 13,
            "axes.labelsize": 12.5,
            "xtick.labelsize": 13,
            "ytick.labelsize": 13,
            "legend.fontsize": 13,
            "axes.linewidth": 1.0,
            "grid.linewidth": 0.7,
        }
    )


def _resolve_path(path: str | Path, project_root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (project_root / candidate).resolve()


def _load_config(config_path: Path) -> Dict[str, Dict[str, str]]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _plot_boxplot(
    ax: plt.Axes,
    labels: List[str],
    data: List[List[float]],
    title: str,
    ylabel: str,
    color: str,
    show_xlabels: bool,
) -> None:
    if not labels:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=12)
        ax.set_axis_off()
        return

    box = ax.boxplot(
        data,
        labels=labels,
        patch_artist=True,
        showmeans=True,
        meanprops={
            "marker": "D",
            "markerfacecolor": "black",
            "markeredgecolor": "black",
            "markersize": 5,
        },
    )
    for patch in box["boxes"]:
        patch.set_facecolor(color)
        patch.set_alpha(0.35)
        patch.set_edgecolor("black")
        patch.set_linewidth(1.0)
    for median in box["medians"]:
        median.set_color("black")
        median.set_linewidth(1.0)
    for whisker in box["whiskers"]:
        whisker.set_color("black")
        whisker.set_linewidth(0.9)
    for cap in box["caps"]:
        cap.set_color("black")
        cap.set_linewidth(0.9)

    if title:
        ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.grid(
        axis="y",
        color="#c0c0c0",
        linestyle="--",
        linewidth=0.7,
        alpha=0.7,
    )
    ax.grid(
        axis="x",
        color="#e0e0e0",
        linestyle="--",
        linewidth=0.6,
        alpha=0.5,
    )
    if show_xlabels:
        ax.tick_params(axis="x", labelrotation=45)
    else:
        ax.tick_params(axis="x", labelbottom=False)
    ax.margins(x=0.01)
    ax.set_facecolor("white")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.9)


def _collect_similarity_data(
    examples_config: Dict[str, Dict[str, str]],
    project_root: Path,
) -> Dict[str, List[List[float]]]:
    collector = MultiExampleCollector()
    for example_name, entry in examples_config.items():
        base_dir = entry.get("base_dir")
        if base_dir:
            base_dir_path = _resolve_path(base_dir, project_root)
        else:
            base_dir_path = project_root

        traces_dir = Path(entry.get("traces_dir", "traces"))
        metrics_dir = Path(entry.get("metrics_dir", "metrics"))
        if not traces_dir.is_absolute():
            traces_dir = base_dir_path / traces_dir
        if not metrics_dir.is_absolute():
            metrics_dir = base_dir_path / metrics_dir

        dataset_example_name = entry.get("dataset_example_name")
        tags = entry.get("tags")
        if isinstance(tags, str):
            tags = [tags]

        print(f"Loading example: {example_name}...")
        try:
            example = collector.add_example(
                example_name=example_name,
                traces_dir=str(traces_dir),
                metrics_dir=str(metrics_dir),
                base_dir=str(base_dir_path) if base_dir_path != project_root else None,
                analysis_mode="per_run",
                compute_ged=False,
                dataset_example_name=dataset_example_name,
                tags=tags,
            )
            num_runs = len(example.call_graphs_per_run) if example.call_graphs_per_run else 0
            print(f"  ✓ Loaded {example_name} ({num_runs} runs)")
        except Exception as exc:
            print(f"  ✗ Error loading {example_name}: {exc}")

    labels: List[str] = []
    jaccard_data: List[List[float]] = []
    lcs_data: List[List[float]] = []

    for example_name in examples_config.keys():
        example = collector.examples.get(example_name)
        if not example:
            continue
        jaccard_values = list(example.pairwise_jaccard or [])
        lcs_values = list(example.pairwise_lcs or [])
        if not jaccard_values and not lcs_values:
            print(f"[{example_name}] missing similarity data; skipped")
            continue
        if not jaccard_values or not lcs_values:
            print(f"[{example_name}] missing jaccard or lcs data; skipped")
            continue
        labels.append(example_name)
        jaccard_data.append(jaccard_values)
        lcs_data.append(lcs_values)
        print(
            f"[{example_name}] pairwise_jaccard={len(jaccard_values)} "
            f"pairwise_lcs={len(lcs_values)}"
        )

    return {
        "labels": labels,
        "jaccard_data": jaccard_data,
        "lcs_data": lcs_data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot average pairwise call graph similarity as stacked boxplots.",
    )
    parser.add_argument(
        "--config",
        default="plot/configs/all_examples_parquet_plot_config.json",
        help="Path to the examples config JSON.",
    )
    parser.add_argument(
        "--output",
        default="plot/figures/comparison/similarity/call_graph_similarity_avg_pairwise.pdf",
        help="Output file path for the plot.",
    )
    args = parser.parse_args()

    config_path = _resolve_path(args.config, _project_root)
    output_path = _resolve_path(args.output, _project_root)

    _apply_paper_style()

    config = _load_config(config_path)
    data = _collect_similarity_data(config, _project_root)

    labels = data["labels"]
    jaccard_data = data["jaccard_data"]
    lcs_data = data["lcs_data"]

    if not labels:
        print("No matching examples with similarity data. Nothing to plot.")
        return 0

    plot_width = max(7, len(labels) * 0.32)
    plot_height = 7
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_jaccard, ax_lcs) = plt.subplots(
        2,
        1,
        figsize=(plot_width, plot_height),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1, 1]},
    )
    _plot_boxplot(
        ax_jaccard,
        labels,
        jaccard_data,
        "",
        "Pairwise Jaccard",
        "#2ecc71",
        show_xlabels=False,
    )
    _plot_boxplot(
        ax_lcs,
        labels,
        lcs_data,
        "",
        "Pairwise LCS",
        "#9b59b6",
        show_xlabels=True,
    )
    # ax_lcs.set_xlabel("Example")

    fig.patch.set_facecolor("white")
    fig.savefig(str(output_path), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
