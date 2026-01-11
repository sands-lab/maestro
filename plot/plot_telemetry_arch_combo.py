#!/usr/bin/env python3
"""Combine cost/duration consistency and accuracy-by-arch from consolidated traces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, List, Optional, Sequence, Tuple

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
    import matplotlib.patches as mpatches  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    plt = None  # type: ignore
    mpatches = None  # type: ignore
    np = None  # type: ignore

ARCH_COLORS: Dict[str, str] = {
    "lats": "#4e79a7",
    "crag": "#e15759",
    "P&E": "#76b041",
}

ARCH_FULL: Dict[str, str] = {
    "lats": "LATS",
    "crag": "CRAG",
    "P&E": "Plan-and-Execute",
}


def load_arch_metrics(config: Path, operations: Sequence[str]) -> Tuple[Dict[str, Dict[str, object]], List[str]]:
    rows, _label_colors, group_order, _label_order = load_runs_from_parquet(
        config,
        operations=operations,
        pricing_file=REPO_ROOT / "tools" / "pricing.json",
        price_per_1m=None,
    )

    data: Dict[str, Dict[str, object]] = {}
    arch_order: List[str] = list(group_order)

    for row in rows:
        arch = str(row.get("group_label") or "unknown")
        tasks = float(row.get("task_count") or 0.0)
        dur = float(row.get("total_duration_seconds") or 0.0)
        cost_total = row.get("cost_total")
        cost_val = float(cost_total) if isinstance(cost_total, (int, float)) else 0.0
        if tasks <= 0:
            continue

        bucket = data.setdefault(
            arch,
            {
                "cost_per_task": [],
                "dur_per_task": [],
                "tasks_total": 0.0,
                "tasks_failed": 0.0,
                "model_stats": {},
            },
        )
        bucket["cost_per_task"].append(cost_val / tasks)
        bucket["dur_per_task"].append(dur / tasks)
        bucket["tasks_total"] += tasks
        bucket["tasks_failed"] += float(row.get("failed_tasks") or 0.0)
        model_name = str(row.get("gen_ai_model") or row.get("run_label") or "unknown")
        ms = bucket["model_stats"].setdefault(model_name, {"tasks_total": 0.0, "tasks_failed": 0.0})
        ms["tasks_total"] += tasks
        ms["tasks_failed"] += float(row.get("failed_tasks") or 0.0)

    return data, arch_order


def plot_combined(
    data: Dict[str, Dict[str, object]],
    arch_order: List[str],
    destination: Path,
    *,
    title: str,
) -> None:
    if plt is None or np is None or mpatches is None:
        return

    arches = [a for a in arch_order if a in data] or sorted(data.keys())
    if not arches:
        return

    positions = np.arange(len(arches)) + 1

    plt.style.use("default")
    fig, axes = plt.subplots(1, 3, figsize=(7.6, 4.6), sharey=True, layout="constrained", facecolor="white")
    for ax in axes:
        ax.set_facecolor("white")

    cost_groups = [np.array(data[a]["cost_per_task"], dtype=float) for a in arches]
    bp_cost = axes[0].boxplot(
        cost_groups,
        vert=False,
        positions=positions,
        patch_artist=True,
        showfliers=True,
        flierprops=dict(marker="o", markersize=4, markerfacecolor="#888", markeredgecolor="#666", alpha=0.5),
    )
    for patch, arch in zip(bp_cost["boxes"], arches):
        patch.set_facecolor(ARCH_COLORS.get(arch, "#ccc"))
        patch.set_alpha(0.65)
    axes[0].set_xlabel("Cost per task ($)", fontsize=14)
    axes[0].set_title("Cost consistency")
    axes[0].grid(True, axis="x", alpha=0.3)
    for spine in axes[0].spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    dur_groups = [np.array(data[a]["dur_per_task"], dtype=float) for a in arches]
    bp_dur = axes[1].boxplot(
        dur_groups,
        vert=False,
        positions=positions,
        patch_artist=True,
        showfliers=True,
        flierprops=dict(marker="o", markersize=4, markerfacecolor="#888", markeredgecolor="#666", alpha=0.5),
    )
    for patch, arch in zip(bp_dur["boxes"], arches):
        patch.set_facecolor(ARCH_COLORS.get(arch, "#ccc"))
        patch.set_alpha(0.65)
    axes[1].set_xlabel("Duration per task (s)", fontsize=14)
    axes[1].set_title("Duration consistency")
    axes[1].grid(True, axis="x", alpha=0.3)
    for spine in axes[1].spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    arch_acc = []
    scatter_x: List[float] = []
    scatter_y: List[float] = []
    for pos, arch in zip(positions, arches):
        bucket = data[arch]
        total = float(bucket.get("tasks_total") or 0.0)
        failed = float(bucket.get("tasks_failed") or 0.0)
        acc = (total - failed) / total * 100.0 if total > 0 else float("nan")
        arch_acc.append(acc)
        model_stats = bucket.get("model_stats") or {}
        for _model, stats in sorted(model_stats.items()):
            m_tasks = float(stats.get("tasks_total") or 0.0)
            m_failed = float(stats.get("tasks_failed") or 0.0)
            if m_tasks <= 0:
                continue
            acc_model = (m_tasks - m_failed) / m_tasks * 100.0
            scatter_x.append(pos + (np.random.rand() - 0.5) * 0.08)
            scatter_y.append(acc_model)

    colors = [ARCH_COLORS.get(a, "#ccc") for a in arches]
    axes[2].barh(positions, arch_acc, color=colors, alpha=0.8, height=0.55, label="Aggregate")
    axes[2].scatter(scatter_y, scatter_x, c="#444", s=20, alpha=0.9, zorder=3, label="Per-run")
    axes[2].set_xlabel("Accuracy (%)", fontsize=14)
    axes[2].set_xlim(0, 105)
    axes[2].set_title("Accuracy by architecture")
    axes[2].grid(True, axis="x", alpha=0.3)
    for spine in axes[2].spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    axes[1].set_yticks(positions, [ARCH_FULL.get(a, a) for a in arches])
    axes[0].set_yticks([])
    axes[2].set_yticks([])

    handles = [mpatches.Patch(color=ARCH_COLORS.get(a, "#ccc"), label=ARCH_FULL.get(a, a)) for a in arches]
    handles.append(mpatches.Patch(color="#444", alpha=0.9, label="Per-model accuracy"))
    legend = fig.legend(
        handles=handles,
        loc="upper center",
        ncol=min(len(handles), 1),
        bbox_to_anchor=(0.17, 0.6),
        frameon=True,
        fancybox=False,
        framealpha=0.95,
        edgecolor="#777",
        fontsize=11,
    )
    legend.get_frame().set_linewidth(0.8)
    fig.suptitle(title, fontsize=16)

    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format=destination.suffix.lstrip(".") or "pdf")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", type=str, default="Cost/Duration/Accuracy, by Architecture")
    parser.add_argument("--operations", nargs="+", default=list(DEFAULT_ALLOWED_OPS))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    data, arch_order = load_arch_metrics(args.config, args.operations)
    plot_combined(data, arch_order, args.output, title=args.title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
