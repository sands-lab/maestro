#!/usr/bin/env python3
"""Per-model cost/duration box plots + accuracy bars."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
from typing import Dict, List, Optional, Sequence

# Allow running from repo root or plot/ directly.
_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.parquet_utils import DEFAULT_ALLOWED_OPS, REPO_ROOT, load_runs_from_parquet

try:  # pragma: no cover
    import matplotlib.pyplot as plt  # type: ignore
except Exception:  # pragma: no cover
    plt = None  # type: ignore

FAMILY_BG = {
    "gpt": "#e6f0ff",
    "gemini": "#fff2d9",
}

MODEL_ABBR = {
    "gemini-2.0-flash-lite": "Ge20FL",
    "gemini-2.5-flash": "Ge25L",
    "gemini-2.5-flash-lite": "Ge25FL",
    "gpt-4o-mini": "G4oM",
    "gpt-5-mini": "G5M",
    "gpt-5-nano": "G5N",
}

ARCH_FULL = {
    "lats": "LATS",
    "crag": "CRAG",
    "P&E": "Plan-and-Execute",
}


def _family_of(model: str) -> Optional[str]:
    lm = model.lower()
    if "gemini" in lm:
        return "gemini"
    if "gpt" in lm:
        return "gpt"
    return None


def wilson(successes: float, total: float, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (float("nan"), float("nan"))
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    lo = max(0.0, (center - margin) * 100.0)
    hi = min(100.0, (center + margin) * 100.0)
    return lo, hi


def load_runs(config: Path, operations: Sequence[str]) -> tuple[List[Dict[str, object]], Dict[str, str], List[str], List[str]]:
    rows, arch_colors, group_order, label_order = load_runs_from_parquet(
        config,
        operations=operations,
        pricing_file=REPO_ROOT / "tools" / "pricing.json",
        price_per_1m=None,
    )
    return rows, arch_colors, group_order, label_order


def aggregate_per_model(
    rows: List[Dict[str, object]]
) -> Dict[str, Dict[str, Dict[str, object]]]:
    data: Dict[str, Dict[str, Dict[str, object]]] = {}
    for r in rows:
        model = str(r.get("group_label") or r.get("gen_ai_model") or "unknown")
        if "embedding" in model.lower():
            continue
        arch = str(r.get("run_label") or "unknown")
        tasks = float(r.get("task_count") or 0.0)
        failed = float(r.get("failed_tasks") or 0.0)
        dur = float(r.get("total_duration_seconds") or 0.0)
        cost_total = r.get("cost_total")
        cost_val = float(cost_total) if isinstance(cost_total, (int, float)) else None
        if tasks <= 0:
            continue
        bucket = data.setdefault(model, {}).setdefault(
            arch,
            {"dur": [], "cost": [], "tasks": 0.0, "failed": 0.0},
        )
        bucket["dur"].append(dur / tasks)
        if cost_val is not None:
            bucket["cost"].append(cost_val / tasks)
        bucket["tasks"] += tasks
        bucket["failed"] += failed
    return data


def plot_fig(
    metrics: Dict[str, Dict[str, object]],
    model_order: List[str],
    arch_order: List[str],
    arch_colors: Dict[str, str],
    destination: Path,
    title: Optional[str],
    show_accuracy_labels: bool = True,
    show_subplot_titles: bool = True,
) -> None:
    if plt is None:
        return
    models = [m for m in model_order if m in metrics]
    if not models:
        return

    sublabels = [a for a in arch_order if any(a in metrics[m] for m in models)]
    if not sublabels:
        sublabels = sorted({a for m in models for a in metrics[m].keys()})

    plt.style.use("default")
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(12, 9),
        sharey=True,
        layout="constrained",
        facecolor="white",
    )
    for ax in axes:
        ax.set_facecolor("white")
    group_spacing = len(sublabels) + 1
    group_centers: List[float] = []

    def _display_label(label: str) -> str:
        return MODEL_ABBR.get(label.lower(), label)

    def _positions_and_data(key: str) -> tuple[List[List[float]], List[float], List[float]]:
        data: List[List[float]] = []
        positions: List[float] = []
        group_centers.clear()
        for gi, model in enumerate(models):
            base = gi * group_spacing
            group_centers.append(base + (len(sublabels) - 1) / 2)
            for si, sub in enumerate(sublabels):
                pos = base + si
                positions.append(pos)
                series = metrics[model].get(sub, {})
                vals = series.get(key, [])
                data.append(vals if vals else [float("nan")])
        return data, positions, list(group_centers)

    def _draw_family_bands(ax) -> None:
        if not models:
            return
        bands = []
        current_family = _family_of(models[0])
        start_idx = 0
        for idx, model in enumerate(models + [""]):
            fam = _family_of(model) if model else None
            if fam != current_family:
                if current_family in FAMILY_BG:
                    bands.append((start_idx, idx - 1, FAMILY_BG[current_family]))
                start_idx = idx
                current_family = fam
        for start, end, color in bands:
            top = end * group_spacing + (len(sublabels) - 0.5)
            bottom = start * group_spacing - 0.5
            ax.axhspan(bottom, top, facecolor=color, alpha=0.65, zorder=0)
        for gi in range(1, len(models)):
            y = gi * group_spacing - 0.95
            ax.axhline(y, color="#bbbbbb", lw=0.5, alpha=0.8, zorder=0.5, linestyle="--")

    for ax, key, xlabel in (
        (axes[0], "cost", "Cost per task ($)"),
        (axes[1], "dur", "Duration per task (s)"),
    ):
        data, positions, centers = _positions_and_data(key)
        bp = ax.boxplot(
            data,
            positions=positions,
            widths=0.6,
            vert=False,
            patch_artist=True,
            showfliers=True,
            flierprops=dict(
                marker="o",
                markersize=4,
                markerfacecolor="#888",
                markeredgecolor="#666",
                alpha=0.5,
            ),
        )
        for idx, patch in enumerate(bp["boxes"]):
            arch = sublabels[idx % len(sublabels)]
            patch.set_facecolor(arch_colors.get(arch, "#999"))
            patch.set_alpha(0.7)
        _draw_family_bands(ax)
        ax.set_xlabel(xlabel, fontsize=20)
        if show_subplot_titles:
            ax.set_title(xlabel, fontsize=16)
        ax.set_yticks(centers, [_display_label(m) for m in models])
        ax.tick_params(axis="y", labelrotation=0, labelsize=16)
        ax.tick_params(axis="x", labelsize=14)
        ax.grid(True, axis="x", alpha=0.3)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(1.0)
            spine.set_color("#333")

    positions: List[float] = []
    heights: List[float] = []
    bar_colors: List[str] = []
    group_centers.clear()
    for gi, model in enumerate(models):
        base = gi * group_spacing
        group_centers.append(base + (len(sublabels) - 1) / 2)
        for si, sub in enumerate(sublabels):
            pos = base + si
            positions.append(pos)
            series = metrics[model].get(sub, {"tasks": 0.0, "failed": 0.0})
            tasks = float(series.get("tasks") or 0.0)
            failed = float(series.get("failed") or 0.0)
            success = tasks - failed
            acc_pct = (success / tasks * 100.0) if tasks > 0 else float("nan")
            heights.append(acc_pct)
            bar_colors.append(arch_colors.get(sub, "#999"))

    axes[2].barh(
        positions,
        heights,
        color=bar_colors,
        alpha=0.7,
        edgecolor="#333",
        linewidth=0.35,
        height=0.55,
    )
    if show_accuracy_labels:
        for y, val in zip(positions, heights):
            if math.isnan(val):
                continue
            axes[2].text(
                val + 1.0,
                y,
                f"{val:.1f}%",
                va="center",
                ha="left",
                fontsize=8,
                color="#333",
            )
    _draw_family_bands(axes[2])
    axes[2].set_yticks(group_centers, [_display_label(m) for m in models])
    axes[2].tick_params(axis="y", labelrotation=0)
    axes[2].set_xlabel("Accuracy (%)", fontsize=20)
    if show_subplot_titles:
        axes[2].set_title("Accuracy (%)", fontsize=20)
    axes[2].set_xlim(0, 105)
    axes[2].tick_params(axis="x", labelsize=14)
    axes[2].grid(True, axis="x", alpha=0.3)
    for spine in axes[2].spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    import matplotlib.patches as mpatches  # type: ignore

    handles = [
        mpatches.Patch(color=arch_colors.get(sub, "#999"), label=ARCH_FULL.get(sub, sub))
        for sub in sublabels
    ]
    if handles:
        legend = fig.legend(
            handles=handles,
            title="Architecture",
            loc="upper center",
            ncol=min(len(handles), 1),
            bbox_to_anchor=(0.26, 0.92),
            fontsize=16,
        )
        legend.get_frame().set_facecolor("white")
        legend.get_frame().set_edgecolor("#333")
        legend.get_frame().set_linewidth(0.9)

    if title:
        fig.suptitle(title, fontsize=18)

    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format=destination.suffix.lstrip(".") or "pdf")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", type=str, default=None)
    parser.add_argument("--hide-accuracy-labels", action="store_true")
    parser.add_argument("--hide-subplot-titles", action="store_true")
    parser.add_argument("--operations", nargs="+", default=list(DEFAULT_ALLOWED_OPS))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    rows, arch_colors, model_order, arch_order = load_runs(args.config, args.operations)
    metrics = aggregate_per_model(rows)
    plot_fig(
        metrics,
        model_order,
        arch_order,
        arch_colors,
        args.output,
        title=args.title,
        show_accuracy_labels=not args.hide_accuracy_labels,
        show_subplot_titles=not args.hide_subplot_titles,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
