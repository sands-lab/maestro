#!/usr/bin/env python3
"""Plot per-example average CPU and memory usage as boxplots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.data_loaders import load_metrics
from .lib.extractors import extract_cpu_memory_usage


METRICS_COLUMNS = (
    "metric_name",
    "data_points",
    "resource",
    "run_id",
)


def _apply_paper_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 15,
            "axes.titlesize": 13,
            "axes.labelsize": 15,
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 15,
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


def _collect_metrics_files(metrics_dir: Path) -> List[Path]:
    if not metrics_dir.exists():
        return []
    return sorted(path for path in metrics_dir.rglob("*.jsonl") if path.is_file())


def _infer_run_id(metrics_file: Path) -> str:
    stem = metrics_file.stem
    match = re.search(r"(\\d{8}_\\d{6})$", stem)
    if match:
        return match.group(1)
    parts = stem.split("_")
    if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
        return f"{parts[-2]}_{parts[-1]}"
    return stem


def _group_metrics_files_by_run(metrics_files: List[Path]) -> Dict[str, List[Path]]:
    grouped: Dict[str, List[Path]] = defaultdict(list)
    for metrics_file in metrics_files:
        grouped[_infer_run_id(metrics_file)].append(metrics_file)
    return dict(grouped)


def _compute_mean_usage_by_run(metrics_files: List[Path]) -> Tuple[List[float], List[float], int]:
    cpu_means: List[float] = []
    mem_means: List[float] = []
    grouped = _group_metrics_files_by_run(metrics_files)
    for run_id in sorted(grouped.keys()):
        run_cpu_total = 0.0
        run_mem_total = 0.0
        has_cpu = False
        has_mem = False
        for metrics_file in grouped[run_id]:
            metrics = load_metrics(str(metrics_file))
            if not metrics:
                continue
            usage = extract_cpu_memory_usage(metrics)
            cpu_values = [v for _, v in usage.get("cpu", {}).get("process", [])]
            mem_values = [v for _, v in usage.get("memory", {}).get("process", [])]
            if cpu_values:
                run_cpu_total += float(np.mean(cpu_values))
                has_cpu = True
            if mem_values:
                run_mem_total += float(np.mean(mem_values))
                has_mem = True
        if has_cpu:
            cpu_means.append(run_cpu_total)
        if has_mem:
            mem_means.append(run_mem_total)
    return cpu_means, mem_means, len(grouped)


def _compute_mean_usage_by_run_parquet(metrics: List[Dict]) -> Tuple[List[float], List[float], int]:
    cpu_means: List[float] = []
    mem_means: List[float] = []
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for metric in metrics:
        run_id = metric.get("run_id") or ""
        grouped[run_id].append(metric)
    for run_id, run_metrics in grouped.items():
        usage = extract_cpu_memory_usage(run_metrics)
        cpu_total = 0.0
        cpu_has = False
        for _, values in (usage.get("cpu", {}).get("per_agent", {}) or {}).items():
            if values:
                cpu_total += float(np.mean([v for _, v in values]))
                cpu_has = True
        if not cpu_has:
            cpu_values = [v for _, v in usage.get("cpu", {}).get("process", [])]
            if cpu_values:
                cpu_total = float(np.mean(cpu_values))
                cpu_has = True
        if cpu_has:
            cpu_means.append(cpu_total)

        mem_total = 0.0
        mem_has = False
        for _, values in (usage.get("memory", {}).get("per_agent", {}) or {}).items():
            if values:
                mem_total += float(np.mean([v for _, v in values]))
                mem_has = True
        if not mem_has:
            mem_values = [v for _, v in usage.get("memory", {}).get("process", [])]
            if mem_values:
                mem_total = float(np.mean(mem_values))
                mem_has = True
        if mem_has:
            mem_means.append(mem_total)
    return cpu_means, mem_means, len(grouped)


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


def _load_config(config_path: Path) -> Dict[str, Dict[str, str]]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot per-example average CPU and memory usage (boxplots) from metrics files.",
    )
    parser.add_argument(
        "--config",
        default="plot/configs/all_examples_parquet_plot_config.json",
        help="Path to the examples config JSON.",
    )
    parser.add_argument(
        "--output",
        default="plot/figures/comparison/cpu_mem/cpu_mem_by_examples.pdf",
        help="Output file path for the plot.",
    )
    args = parser.parse_args()

    config_path = _resolve_path(args.config, _project_root)
    output_path = _resolve_path(args.output, _project_root)

    _apply_paper_style()

    config = _load_config(config_path)
    cpu_by_example: Dict[str, List[float]] = {}
    mem_by_example: Dict[str, List[float]] = {}

    for example_name, entry in config.items():
        base_dir_value = entry.get("base_dir", _project_root)
        base_dir = _resolve_path(base_dir_value, _project_root)
        metrics_path = Path(entry["metrics_dir"])
        if not metrics_path.is_absolute():
            metrics_path = base_dir / metrics_path

        if metrics_path.suffix.lower() == ".parquet":
            dataset_example_name = entry.get("dataset_example_name", example_name)
            tags = entry.get("tags")
            if isinstance(tags, str):
                tags = [tags]
            metrics = load_metrics(
                str(metrics_path),
                example_name=dataset_example_name,
                tags=tags,
                columns=METRICS_COLUMNS,
            )
            cpu_means, mem_means, run_count = _compute_mean_usage_by_run_parquet(metrics)
            metrics_files = []
        else:
            metrics_files = _collect_metrics_files(metrics_path)
            cpu_means, mem_means, run_count = _compute_mean_usage_by_run(metrics_files)

        cpu_by_example[example_name] = cpu_means
        mem_by_example[example_name] = mem_means

        print(
            f"[{example_name}] metrics_files={len(metrics_files)} "
            f"runs={run_count} cpu_runs={len(cpu_means)} mem_runs={len(mem_means)}"
        )

    content_creation_label = "Content Creat."
    other_mem_values: List[float] = []
    for example_name, mem_values in mem_by_example.items():
        if example_name == content_creation_label:
            continue
        other_mem_values.extend(mem_values)

    if other_mem_values:
        other_mem_mean = float(np.mean(other_mem_values))
        print(
            f"[{content_creation_label} excluded] mem_mean={other_mem_mean:.2f} "
            f"(n={len(other_mem_values)})"
        )
    else:
        print(f"[{content_creation_label} excluded] mem_mean=N/A (no data)")

    content_mem_values = mem_by_example.get(content_creation_label, [])
    if content_mem_values:
        content_mem_mean = float(np.mean(content_mem_values))
        print(
            f"[{content_creation_label}] mem_mean={content_mem_mean:.2f} "
            f"(n={len(content_mem_values)})"
        )
    else:
        print(f"[{content_creation_label}] mem_values=N/A (no data)")

    labels: List[str] = []
    cpu_data: List[List[float]] = []
    mem_data: List[List[float]] = []
    for example_name in config.keys():
        cpu_values = cpu_by_example.get(example_name, [])
        mem_values = mem_by_example.get(example_name, [])
        if not cpu_values and not mem_values:
            continue
        if not cpu_values or not mem_values:
            print(f"[{example_name}] missing cpu or mem data; skipped in combined plot")
            continue
        labels.append(example_name)
        cpu_data.append(cpu_values)
        mem_data.append(mem_values)

    if not labels:
        print("No matching examples with both CPU and memory data. Nothing to plot.")
        return 0

    plot_width = max(7, len(labels) * 0.32)
    plot_height = 7
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_cpu, ax_mem) = plt.subplots(
        2,
        1,
        figsize=(plot_width, plot_height),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1, 1]},
    )
    _plot_boxplot(
        ax_cpu,
        labels,
        cpu_data,
        "",
        "CPU (%)",
        "#4c78a8",
        show_xlabels=False,
    )
    _plot_boxplot(
        ax_mem,
        labels,
        mem_data,
        "",
        "Memory (MB)",
        "#f58518",
        show_xlabels=True,
    )
    # ax_mem.set_xlabel("Example")

    fig.patch.set_facecolor("white")
    fig.savefig(str(output_path), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
METRICS_COLUMNS = (
    "metric_name",
    "data_points",
    "resource",
    "run_id",
)
