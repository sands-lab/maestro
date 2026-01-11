#!/usr/bin/env python3
"""Compare CRAG/LATS/Plan-and-Execute across models from consolidated traces."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from statistics import median
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Allow running from repo root or plot/ directly.
_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.parquet_utils import (
    DEFAULT_ALLOWED_OPS,
    REPO_ROOT,
    load_pricing,
    load_runs_from_parquet,
)

try:  # pragma: no cover - plotting optional
    import matplotlib.pyplot as plt  # type: ignore
    import matplotlib.patches as mpatches  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - plotting optional
    plt = None  # type: ignore
    mpatches = None  # type: ignore
    np = None  # type: ignore

DEFAULT_ARCH_COLORS: Dict[str, str] = {
    "lats": "#4e79a7",
    "crag": "#e15759",
    "P&E": "#76b041",
}
FULL_ARCH_NAMES: Dict[str, str] = {
    "lats": "LATS",
    "crag": "CRAG",
    "P&E": "Plan-and-Execute",
}


def _safe_div(numer: float, denom: float) -> Optional[float]:
    if denom <= 0:
        return None
    return numer / denom


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    filtered = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not filtered:
        return None
    return float(median(filtered))


def _fmt_float(value: Optional[float]) -> Optional[float]:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def build_scorecard(
    config_path: Path,
    *,
    operations: Sequence[str],
    pricing_file: Optional[Path],
    price_per_1m: Optional[float],
) -> Tuple[List[Dict[str, object]], Dict[str, str]]:
    rows, label_colors, _group_order, _label_order = load_runs_from_parquet(
        config_path,
        operations=operations,
        pricing_file=pricing_file,
        price_per_1m=price_per_1m,
    )

    buckets: Dict[Tuple[str, str], Dict[str, object]] = {}
    for row in rows:
        model_group = str(row.get("group_label") or "unknown")
        arch = str(row.get("run_label") or "unknown")
        bucket = buckets.setdefault(
            (model_group, arch),
            {
                "model": model_group,
                "arch": arch,
                "runs": 0,
                "tasks_total": 0,
                "tasks_failed": 0,
                "dur_s_per_task": [],
                "tokens_per_task": [],
                "cost_per_task": [],
            },
        )
        tasks = int(row.get("task_count") or 0)
        failed = int(row.get("failed_tasks") or 0)
        duration_s = float(row.get("total_duration_seconds") or 0.0)
        tokens = float(row.get("total_tokens") or 0.0)
        cost_total = row.get("cost_total")
        cost_total_f = float(cost_total) if isinstance(cost_total, (int, float)) else None

        bucket["runs"] = int(bucket["runs"]) + 1
        bucket["tasks_total"] = int(bucket["tasks_total"]) + tasks
        bucket["tasks_failed"] = int(bucket["tasks_failed"]) + failed
        bucket["dur_s_per_task"].append(_safe_div(duration_s, float(tasks)))
        bucket["tokens_per_task"].append(_safe_div(tokens, float(tasks)))
        bucket["cost_per_task"].append(_safe_div(cost_total_f, float(tasks)) if cost_total_f is not None else None)

    scorecard: List[Dict[str, object]] = []
    for (_model, _arch), bucket in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        tasks_total = int(bucket["tasks_total"])
        tasks_failed = int(bucket["tasks_failed"])
        accuracy = (1.0 - tasks_failed / tasks_total) * 100.0 if tasks_total else None
        scorecard.append(
            {
                "model": bucket["model"],
                "arch": bucket["arch"],
                "runs": int(bucket["runs"]),
                "tasks_total": tasks_total,
                "tasks_failed": tasks_failed,
                "accuracy_pct": _fmt_float(accuracy),
                "median_latency_s_per_task": _fmt_float(_median(bucket["dur_s_per_task"])),
                "median_tokens_per_task": _fmt_float(_median(bucket["tokens_per_task"])),
                "median_cost_per_task": _fmt_float(_median(bucket["cost_per_task"])),
            }
        )

    return scorecard, label_colors


def _resolve_arch_color(arch: str, label_colors: Optional[Dict[str, str]]) -> str:
    if label_colors:
        color = label_colors.get(arch)
        if color:
            return color
    return DEFAULT_ARCH_COLORS.get(arch, "#4b8bbe")


def _display_arch_name(arch: str) -> str:
    return FULL_ARCH_NAMES.get(arch, arch)


def _accuracy_bucket(acc: Optional[float]) -> str:
    if acc is None:
        return "unknown"
    if acc >= 75:
        return "high"
    return "other"


def plot_overview(
    scorecard: List[Dict[str, object]],
    destination: Path,
    *,
    label_colors: Optional[Dict[str, str]] = None,
    title: str,
    skip_crowded_labels: bool = False,
    label_distance_threshold: float = 0.2,
    accuracy_shape_buckets: bool = False,
) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.patches as mpatches  # type: ignore
    except Exception:
        return

    points = [
        row
        for row in scorecard
        if isinstance(row.get("median_latency_s_per_task"), (int, float))
        and isinstance(row.get("median_cost_per_task"), (int, float))
        and isinstance(row.get("accuracy_pct"), (int, float))
    ]
    if not points:
        return

    xs = [float(row["median_latency_s_per_task"]) for row in points]
    ys = [float(row["median_cost_per_task"]) for row in points]
    x_range = max(xs) - min(xs) or 1.0
    y_range = max(ys) - min(ys) or 1.0

    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(5.6, 6), layout="constrained", facecolor="white")
    ax.set_facecolor("white")
    seen_arches: Dict[str, str] = {}
    offsets = [(6, 6), (6, -10), (-10, 6), (-10, -10), (12, 0), (-12, 0), (0, 10), (0, -14)]
    offset_idx: Dict[str, int] = {}

    def nearest_norm_distance(idx: int) -> float:
        x0, y0 = xs[idx], ys[idx]
        best = float("inf")
        for j, (x1, y1) in enumerate(zip(xs, ys)):
            if j == idx:
                continue
            dx = (x0 - x1) / x_range
            dy = (y0 - y1) / y_range
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best:
                best = dist
        return best

    for idx, row in enumerate(points):
        arch = str(row.get("arch") or "unknown")
        model = str(row.get("model") or "unknown")
        acc = float(row.get("accuracy_pct") or 0.0)
        color = _resolve_arch_color(arch, label_colors)
        seen_arches[arch] = color
        marker = "o"
        if accuracy_shape_buckets:
            marker = "s" if _accuracy_bucket(acc) == "high" else "o"
        ax.scatter(xs[idx], ys[idx], s=110, color=color, marker=marker, alpha=0.9)

        if skip_crowded_labels and nearest_norm_distance(idx) < label_distance_threshold:
            continue
        offset = offsets[offset_idx.get(model, 0) % len(offsets)]
        offset_idx[model] = offset_idx.get(model, 0) + 1
        ax.annotate(
            _display_arch_name(arch),
            (xs[idx], ys[idx]),
            textcoords="offset points",
            xytext=offset,
            fontsize=10,
            fontweight="bold",
        )

    ax.set_xlabel("Median duration per task (s)", fontsize=12)
    ax.set_ylabel("Median cost per task ($)", fontsize=12)
    ax.set_title(title, fontsize=13)
    ax.grid(True, alpha=0.3)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    handles = [mpatches.Patch(color=color, label=_display_arch_name(arch)) for arch, color in seen_arches.items()]
    if accuracy_shape_buckets:
        from matplotlib.lines import Line2D  # type: ignore

        shape_handles = [
            Line2D([0], [0], marker="o", color="black", linestyle="None", markersize=7, label="acc ≥ 0.75"),
            Line2D([0], [0], marker="s", color="black", linestyle="None", markersize=7, label="< 0.75"),
        ]
        handles.extend(shape_handles)
    if handles:
        ax.legend(handles=handles, title="Architecture / Accuracy", loc="lower right", fontsize=10, frameon=True)

    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format=destination.suffix.lstrip(".") or "pdf")


def plot_detailed(
    scorecard: List[Dict[str, object]],
    destination: Path,
    *,
    title: str,
    label_colors: Optional[Dict[str, str]] = None,
    skip_crowded_labels: bool = False,
    label_distance_threshold: float = 0.2,
    accuracy_shape_buckets: bool = False,
) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return

    points = [
        row
        for row in scorecard
        if isinstance(row.get("median_latency_s_per_task"), (int, float))
        and isinstance(row.get("median_cost_per_task"), (int, float))
        and isinstance(row.get("accuracy_pct"), (int, float))
    ]
    if not points:
        return

    xs = [float(row["median_latency_s_per_task"]) for row in points]
    ys = [float(row["median_cost_per_task"]) for row in points]
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(7, 6), layout="constrained", facecolor="white")
    ax.set_facecolor("white")

    for idx, row in enumerate(points):
        arch = str(row.get("arch") or "unknown")
        model = str(row.get("model") or "unknown")
        acc = float(row.get("accuracy_pct") or 0.0)
        color = _resolve_arch_color(arch, label_colors)
        marker = "o"
        if accuracy_shape_buckets:
            marker = "s" if _accuracy_bucket(acc) == "high" else "o"
        ax.scatter(xs[idx], ys[idx], s=115, color=color, marker=marker, alpha=0.9)

        if skip_crowded_labels:
            x_range = max(xs) - min(xs) or 1.0
            y_range = max(ys) - min(ys) or 1.0
            best = float("inf")
            x0, y0 = xs[idx], ys[idx]
            for j, (x1, y1) in enumerate(zip(xs, ys)):
                if j == idx:
                    continue
                dx = (x0 - x1) / x_range
                dy = (y0 - y1) / y_range
                dist = (dx * dx + dy * dy) ** 0.5
                best = min(best, dist)
            if best < label_distance_threshold:
                continue

        ax.annotate(
            f"{model}\n{_display_arch_name(arch)}",
            (xs[idx], ys[idx]),
            textcoords="offset points",
            xytext=(6, 6),
            fontsize=9,
        )

    ax.set_xlabel("Median duration per task (s)", fontsize=12)
    ax.set_ylabel("Median cost per task ($)", fontsize=12)
    ax.set_title(title, fontsize=13)
    ax.grid(True, alpha=0.3)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("#333")

    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, format=destination.suffix.lstrip(".") or "pdf")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plot-output", type=Path)
    parser.add_argument("--csv-output", type=Path)
    parser.add_argument("--plot-mode", choices=("overview", "detailed"), default="overview")
    parser.add_argument("--skip-crowded-labels", action="store_true")
    parser.add_argument("--label-distance-threshold", type=float, default=0.2)
    parser.add_argument("--accuracy-shape-buckets", action="store_true")
    parser.add_argument("--title", type=str, default="Latency vs Cost per Task, by Arch.")
    parser.add_argument("--pricing-file", type=Path)
    parser.add_argument("--price-per-1m", type=float)
    parser.add_argument("--operations", nargs="+", default=list(DEFAULT_ALLOWED_OPS))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    pricing = args.pricing_file
    if pricing is None:
        default_pricing = REPO_ROOT / "tools" / "pricing.json"
        pricing = default_pricing if default_pricing.exists() else None

    scorecard, label_colors = build_scorecard(
        args.config,
        operations=args.operations,
        pricing_file=pricing,
        price_per_1m=args.price_per_1m,
    )

    if args.csv_output:
        import csv
        args.csv_output.parent.mkdir(parents=True, exist_ok=True)
        with args.csv_output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(scorecard[0].keys()) if scorecard else [],
            )
            writer.writeheader()
            writer.writerows(scorecard)

    if args.plot_output:
        if args.plot_mode == "overview":
            plot_overview(
                scorecard,
                args.plot_output,
                label_colors=label_colors,
                title=args.title,
                skip_crowded_labels=args.skip_crowded_labels,
                label_distance_threshold=args.label_distance_threshold,
                accuracy_shape_buckets=args.accuracy_shape_buckets,
            )
        else:
            plot_detailed(
                scorecard,
                args.plot_output,
                label_colors=label_colors,
                title=args.title,
                skip_crowded_labels=args.skip_crowded_labels,
                label_distance_threshold=args.label_distance_threshold,
                accuracy_shape_buckets=args.accuracy_shape_buckets,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
