#!/usr/bin/env python3
"""Visualize the impact of enabling web search (Tavily) using consolidated traces."""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path
from statistics import median
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# Allow running from repo root or plot/ directly.
_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.parquet_plot_utils import should_show_label
from .lib.parquet_utils import DEFAULT_ALLOWED_OPS, REPO_ROOT, load_runs_from_parquet

try:  # pragma: no cover
    import matplotlib.pyplot as plt  # type: ignore
    import matplotlib.patches as mpatches  # type: ignore
    from matplotlib import colors as mcolors  # type: ignore
    from matplotlib.lines import Line2D  # type: ignore
    from matplotlib.patches import Rectangle  # type: ignore
except Exception:  # pragma: no cover
    plt = None  # type: ignore
    mpatches = None  # type: ignore
    mcolors = None  # type: ignore
    Line2D = None  # type: ignore
    Rectangle = None  # type: ignore

ARCH_COLORS: Dict[str, str] = {
    "lats": "#4e79a7",
    "crag": "#e15759",
    "P&E": "#76b041",
}
ARCH_MARKERS: Dict[str, str] = {
    "lats": "o",
    "crag": "s",
    "P&E": "D",
}

ARCH_FULL: Dict[str, str] = {
    "lats": "LATS",
    "crag": "CRAG",
    "P&E": "Plan-and-Execute",
}

MODEL_ABBR = {
    "gemini-2.0-flash-lite": "Ge20FL",
    "gemini-2.5-flash": "Ge25F",
    "gemini-2.5-flash-lite": "Ge25FL",
    "gpt-4o-mini": "G4oM",
    "gpt-5-mini": "G5M",
    "gpt-5-nano": "G5N",
    "gpt-oss-120b": "OSS120B",
}

MODEL_COLORS_ORDERED = [
    ("gpt-5-mini", "#1f77b4"),
    ("gpt-5-nano", "#6baed6"),
    ("gpt-4o-mini", "#9ecae1"),
    ("gemini-2.5-flash", "#e15759"),
    ("gemini-2.5-flash-lite", "#f28e2b"),
    ("gemini-2.0-flash-lite", "#edc948"),
]

MODEL_FAMILY_COLORS = {
    "gpt": ["#1f77b4", "#4e9ad6", "#7ab4ff", "#9ec2ff"],
    "gemini": ["#ff7f0e", "#f2a541", "#f6c56b", "#f8d89b"],
    "other": ["#8c564b", "#bc8f8f", "#c7b0a6", "#a6761d"],
}


def _safe_div(numer: float, denom: float) -> Optional[float]:
    if denom <= 0:
        return None
    return numer / denom


def _median(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    if not vals:
        return None
    return float(median(vals))


def _desaturate_color(hex_color: str, factor: float = 0.55) -> str:
    rgb = mcolors.to_rgb(hex_color)
    gray = sum(rgb) / 3.0
    blend = tuple(gray * factor + c * (1 - factor) for c in rgb)
    return mcolors.to_hex(blend)


def _assign_model_colors(models: List[str], grayscale: bool = False) -> Dict[str, str]:
    if grayscale:
        levels = np.linspace(0.2, 0.85, max(1, len(models)))
        return {m: mcolors.to_hex((g, g, g)) for m, g in zip(sorted(models), levels)}

    ordered = {}
    for name, clr in MODEL_COLORS_ORDERED:
        if name in models:
            ordered[name] = clr
    families: Dict[str, List[str]] = {"gpt": [], "gemini": [], "other": []}
    for m in sorted(models):
        lower = m.lower()
        if "gemini" in lower:
            families["gemini"].append(m)
        elif "gpt" in lower:
            families["gpt"].append(m)
        else:
            families["other"].append(m)
    colors: Dict[str, str] = dict(ordered)
    for fam, names in families.items():
        palette = MODEL_FAMILY_COLORS.get(fam, MODEL_FAMILY_COLORS["other"])
        for idx, name in enumerate(names):
            colors.setdefault(name, palette[idx % len(palette)])
    return colors


def _load_metrics(
    config: Path,
    *,
    operations: Sequence[str],
    pricing_file: Optional[Path],
    price_per_1m: Optional[float],
) -> Dict[Tuple[str, str], Dict[str, Optional[float]]]:
    rows, _label_colors, _group_order, _label_order = load_runs_from_parquet(
        config,
        operations=operations,
        pricing_file=pricing_file,
        price_per_1m=price_per_1m,
    )

    buckets: Dict[Tuple[str, str], Dict[str, List[Optional[float]]]] = {}
    for r in rows:
        arch = str(r.get("group_label") or "unknown")
        model = str(r.get("run_label") or "unknown")
        key = (arch, model)
        b = buckets.setdefault(
            key,
            {
                "lat": [],
                "tokens": [],
                "cost": [],
                "acc": [],
                "tasks": 0.0,
                "failed": 0.0,
            },
        )
        tasks = float(r.get("task_count") or 0.0)
        duration = float(r.get("total_duration_seconds") or 0.0)
        tokens = float(r.get("total_tokens") or 0.0)
        cost_total = r.get("cost_total")
        cost_f = float(cost_total) if isinstance(cost_total, (int, float)) else None
        failed = float(r.get("failed_tasks") or 0.0)
        acc = (1.0 - failed / tasks) * 100.0 if tasks > 0 else None

        b["lat"].append(_safe_div(duration, tasks))
        b["tokens"].append(_safe_div(tokens, tasks))
        b["cost"].append(_safe_div(cost_f, tasks) if cost_f is not None else None)
        b["acc"].append(acc)
        b["tasks"] += tasks
        b["failed"] += failed

    medians: Dict[Tuple[str, str], Dict[str, Optional[float]]] = {}
    for key, b in buckets.items():
        medians[key] = {
            "lat": _median(b["lat"]),
            "tokens": _median(b["tokens"]),
            "cost": _median(b["cost"]),
            "acc": _median(b["acc"]),
            "tasks": float(b["tasks"]),
            "failed": float(b["failed"]),
        }
    return medians


def plot_tavily_diff(
    with_metrics: Dict[Tuple[str, str], Dict[str, Optional[float]]],
    without_metrics: Dict[Tuple[str, str], Dict[str, Optional[float]]],
    *,
    scatter_output: Optional[Path],
    facet_output: Optional[Path],
    accuracy_output: Optional[Path],
    relative_percent: bool,
    color_mode: str,
    shade_arch_background: bool,
    arch_background_alpha: float,
    arch_boundary_color: str,
    no_arch_boundary: bool,
    title: Optional[str],
    skip_crowded_labels: bool,
    label_distance_threshold: float,
    arrow_distance_threshold: float,
) -> None:
    if plt is None or mcolors is None or Rectangle is None:
        return

    keys = sorted(set(with_metrics) & set(without_metrics))
    if not keys:
        return

    arches = sorted({arch for arch, _ in keys})
    models = sorted({model for _arch, model in keys})

    model_colors = _assign_model_colors(models, grayscale=(color_mode == "grayscale"))

    points = []
    for arch, model in keys:
        with_row = with_metrics[(arch, model)]
        wo_row = without_metrics[(arch, model)]
        if with_row.get("lat") is None or wo_row.get("lat") is None:
            continue
        if with_row.get("cost") is None or wo_row.get("cost") is None:
            continue
        points.append((arch, model, wo_row, with_row))

    if not points:
        return

    def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None:
            return None
        return b - a

    def _delta_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None or a == 0:
            return None
        return (b - a) / a * 100.0

    def _prep_xy(row: Dict[str, Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
        return row.get("lat"), row.get("cost")

    def _prep_delta(a: Dict[str, Optional[float]], b: Dict[str, Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
        if relative_percent:
            return _delta_pct(a.get("lat"), b.get("lat")), _delta_pct(a.get("cost"), b.get("cost"))
        return _delta(a.get("lat"), b.get("lat")), _delta(a.get("cost"), b.get("cost"))

    if scatter_output:
        plt.style.use("default")
        fig, ax = plt.subplots(figsize=(6.2, 5.8), layout="constrained", facecolor="white")
        ax.set_facecolor("white")

        xs: List[float] = []
        ys: List[float] = []
        arch_to_points: Dict[str, List[Tuple[float, float]]] = {}
        prepared: List[Tuple[str, str, Tuple[float, float], Tuple[float, float]]] = []
        for arch, model, wo_row, with_row in points:
            x0, y0 = _prep_xy(wo_row)
            x1, y1 = _prep_xy(with_row)
            if x0 is None or y0 is None or x1 is None or y1 is None:
                continue
            if relative_percent:
                dx, dy = _prep_delta(wo_row, with_row)
                if dx is None or dy is None:
                    continue
                base_point = (0.0, 0.0)
                var_point = (float(dx), float(dy))
            else:
                base_point = (float(x0), float(y0))
                var_point = (float(x1), float(y1))
            prepared.append((arch, model, base_point, var_point))
            xs.append(var_point[0])
            ys.append(var_point[1])
            arch_to_points.setdefault(arch, []).append(var_point)

        for arch, model, base_point, var_point in prepared:
            marker = ARCH_MARKERS.get(arch, "o")
            color = model_colors.get(model, "#4b8bbe")
            if not relative_percent:
                ax.scatter(
                    *base_point,
                    s=80,
                    marker=marker,
                    color=_desaturate_color(color),
                    alpha=0.7,
                )
            ax.scatter(
                *var_point,
                s=100,
                marker=marker,
                color=color,
                edgecolor="#333",
                linewidth=0.6,
            )
            if not relative_percent:
                dx = var_point[0] - base_point[0]
                dy = var_point[1] - base_point[1]
                if abs(dx) + abs(dy) < arrow_distance_threshold:
                    continue
                ax.annotate(
                    "",
                    xy=var_point,
                    xytext=base_point,
                    arrowprops=dict(arrowstyle="->", color="#666", alpha=0.7, linewidth=1.1),
                )

        if shade_arch_background and arch_to_points:
            arch_points = {arch: np.array(pts) for arch, pts in arch_to_points.items() if pts}
            if arch_points:
                x_lo, x_hi = min(xs), max(xs)
                y_lo, y_hi = min(ys), max(ys)
                x_pad = (x_hi - x_lo) * 0.08 or 1.0
                y_pad = (y_hi - y_lo) * 0.08 or 1.0
                x_lo -= x_pad
                x_hi += x_pad
                y_lo -= y_pad
                y_hi += y_pad
                arch_list = list(arch_points.keys())
                grid_res = 250
                gx = np.linspace(x_lo, x_hi, grid_res)
                gy = np.linspace(y_lo, y_hi, grid_res)
                xx, yy = np.meshgrid(gx, gy)
                coords = np.stack([xx.ravel(), yy.ravel()], axis=1)
                dists = np.zeros((coords.shape[0], len(arch_list)))
                for idx, arch in enumerate(arch_list):
                    pts = arch_points[arch]
                    deltas = coords[:, None, :] - pts[None, :, :]
                    d_sq = np.sum(deltas * deltas, axis=2)
                    dists[:, idx] = np.min(d_sq, axis=1)
                region = np.argmin(dists, axis=1).reshape(xx.shape)
                rgba = np.zeros(xx.shape + (4,))
                for idx, arch in enumerate(arch_list):
                    base_rgba = list(mcolors.to_rgba(ARCH_COLORS.get(arch, "#4b8bbe")))
                    base_rgba[3] = arch_background_alpha
                    rgba[region == idx] = base_rgba
                ax.imshow(
                    rgba,
                    extent=[x_lo, x_hi, y_lo, y_hi],
                    origin="lower",
                    aspect="auto",
                    zorder=0,
                )
                if not no_arch_boundary and len(arch_list) > 1:
                    levels = np.arange(-0.5, len(arch_list))
                    ax.contour(
                        xx,
                        yy,
                        region,
                        levels=levels,
                        colors=arch_boundary_color,
                        linewidths=2.2,
                        alpha=0.9,
                        zorder=1,
                    )
                for arch, pts in arch_points.items():
                    if len(pts) == 0:
                        continue
                    cx, cy = float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1]))
                    x_range = max(x_hi - x_lo, 1e-6)
                    y_range = max(y_hi - y_lo, 1e-6)
                    if arch == "lats":
                        cx -= 0.06 * x_range
                        cy += 0.06 * y_range
                    ax.text(
                        cx,
                        cy,
                        ARCH_FULL.get(arch, arch),
                        ha="center",
                        va="center",
                        fontsize=11,
                        fontweight="bold",
                        color=ARCH_COLORS.get(arch, "#333"),
                        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.6),
                        zorder=5,
                    )

        for idx, (arch, model, _base_point, var_point) in enumerate(prepared):
            if skip_crowded_labels and not should_show_label(xs, ys, idx, label_distance_threshold):
                continue
            label = MODEL_ABBR.get(model.lower(), model)
            ax.annotate(label, var_point, textcoords="offset points", xytext=(6, 6), fontsize=8)

        if relative_percent:
            ax.set_xlabel("Duration change (%)", fontsize=12)
            ax.set_ylabel("Cost change (%)", fontsize=12)
        else:
            ax.set_xlabel("Latency per task (s)", fontsize=12)
            ax.set_ylabel("Cost per task ($)", fontsize=12)
        if title:
            ax.set_title(title, fontsize=13)
        ax.grid(True, alpha=0.3)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(1.0)
            spine.set_color("#333")

        legend_handles = []
        if color_mode == "arch":
            for arch in arches:
                legend_handles.append(
                    Line2D(
                        [0],
                        [0],
                        marker=ARCH_MARKERS.get(arch, "o"),
                        color="w",
                        label=ARCH_FULL.get(arch, arch),
                        markerfacecolor=ARCH_COLORS.get(arch, "#ddd"),
                        markersize=9,
                    )
                )
        else:
            for model in models:
                legend_handles.append(
                    Line2D(
                        [0],
                        [0],
                        marker="o",
                        color="w",
                        label=MODEL_ABBR.get(model.lower(), model),
                        markerfacecolor=model_colors.get(model, "#4b8bbe"),
                        markersize=8,
                    )
                )
        if legend_handles:
            legend_title = "Architecture" if color_mode == "arch" else "Model"
            legend = ax.legend(handles=legend_handles, loc="center right", frameon=True, title=legend_title)
            legend.get_frame().set_facecolor("white")
            legend.get_frame().set_edgecolor("#333")
            legend.get_frame().set_linewidth(0.9)

        scatter_output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(scatter_output, format=scatter_output.suffix.lstrip(".") or "pdf")

    if facet_output:
        plt.style.use("default")
        fig, axes = plt.subplots(
            1,
            len(arches),
            figsize=(5.0 * len(arches), 4.5),
            sharey=True,
            layout="constrained",
            facecolor="white",
        )
        for ax in axes:
            ax.set_facecolor("white")
        if len(arches) == 1:
            axes = [axes]
        for ax, arch in zip(axes, arches):
            for _arch, model, wo_row, with_row in points:
                if _arch != arch:
                    continue
                x0, y0 = _prep_xy(wo_row)
                x1, y1 = _prep_xy(with_row)
                if x0 is None or y0 is None or x1 is None or y1 is None:
                    continue
                color = model_colors.get(model, "#4b8bbe")
                if relative_percent:
                    dx, dy = _prep_delta(wo_row, with_row)
                    if dx is None or dy is None:
                        continue
                    base_point = (0.0, 0.0)
                    var_point = (float(dx), float(dy))
                else:
                    base_point = (float(x0), float(y0))
                    var_point = (float(x1), float(y1))
                if not relative_percent:
                    ax.scatter(*base_point, s=80, color=_desaturate_color(color), alpha=0.7)
                ax.scatter(*var_point, s=100, color=color, edgecolor="#333", linewidth=0.6)
                if not relative_percent:
                    dx = var_point[0] - base_point[0]
                    dy = var_point[1] - base_point[1]
                    if abs(dx) + abs(dy) < arrow_distance_threshold:
                        continue
                    ax.annotate(
                        "",
                        xy=var_point,
                        xytext=base_point,
                        arrowprops=dict(arrowstyle="->", color="#666", alpha=0.7, linewidth=1.1),
                    )
            ax.set_title(ARCH_FULL.get(arch, arch))
            ax.grid(True, alpha=0.3)
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_linewidth(1.0)
                spine.set_color("#333")
            if relative_percent:
                ax.set_xlabel("Duration change (%)")
            else:
                ax.set_xlabel("Duration/task (s)")
        if relative_percent:
            axes[0].set_ylabel("Cost change (%)")
        else:
            axes[0].set_ylabel("Cost/task ($)")

        facet_output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(facet_output, format=facet_output.suffix.lstrip(".") or "pdf")

    if accuracy_output:
        plt.style.use("default")
        fig, ax = plt.subplots(figsize=(3.6, 4), layout="constrained", facecolor="white")
        ax.set_facecolor("white")
        deltas: List[float] = []
        colors: List[str] = []
        per_point_x: List[float] = []
        per_point_y: List[float] = []
        for idx, arch in enumerate(arches):
            base_tasks = sum(v.get("tasks", 0.0) for k, v in without_metrics.items() if k[0] == arch)
            base_failed = sum(v.get("failed", 0.0) for k, v in without_metrics.items() if k[0] == arch)
            var_tasks = sum(v.get("tasks", 0.0) for k, v in with_metrics.items() if k[0] == arch)
            var_failed = sum(v.get("failed", 0.0) for k, v in with_metrics.items() if k[0] == arch)
            base_acc = (1.0 - base_failed / base_tasks) * 100.0 if base_tasks > 0 else None
            var_acc = (1.0 - var_failed / var_tasks) * 100.0 if var_tasks > 0 else None
            delta = (var_acc - base_acc) if (base_acc is not None and var_acc is not None) else None
            deltas.append(delta if delta is not None else 0.0)
            colors.append(ARCH_COLORS.get(arch, "#4b8bbe"))
            models = {k[1] for k in without_metrics.keys() if k[0] == arch} | {k[1] for k in with_metrics.keys() if k[0] == arch}
            for m in models:
                b = without_metrics.get((arch, m), {})
                v = with_metrics.get((arch, m), {})
                b_tasks = float(b.get("tasks") or 0.0)
                b_failed = float(b.get("failed") or 0.0)
                v_tasks = float(v.get("tasks") or 0.0)
                v_failed = float(v.get("failed") or 0.0)
                if b_tasks > 0 and v_tasks > 0:
                    a_b = (1.0 - b_failed / b_tasks) * 100.0
                    a_v = (1.0 - v_failed / v_tasks) * 100.0
                    per_point_x.append(idx + (0.12 * (0.5 - random.random())))
                    per_point_y.append(a_v - a_b)

        x = list(range(len(arches)))
        ax.bar(x, deltas, color=colors, alpha=0.75)
        if per_point_x and per_point_y:
            ax.scatter(per_point_x, per_point_y, c="#222", s=18, alpha=0.85, zorder=3, label="Per-model delta")
        ax.axhline(0, color="#666", lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels([ARCH_FULL.get(a, a) for a in arches], rotation=10)
        ax.set_ylabel("Accuracy delta (pct points, with - without)")
        ax.set_title("Accuracy delta, by Architecture")
        ax.grid(True, axis="y", alpha=0.3)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(1.0)
            spine.set_color("#333")

        if per_point_x:
            import matplotlib.patches as mpatches  # type: ignore

            legend = ax.legend(handles=[mpatches.Patch(color="#222", label="Per-model delta")], loc="best")
            legend.get_frame().set_facecolor("white")
            legend.get_frame().set_edgecolor("#333")
            legend.get_frame().set_linewidth(0.9)

        accuracy_output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(accuracy_output, format=accuracy_output.suffix.lstrip(".") or "pdf")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-config", type=Path, required=True)
    parser.add_argument("--without-config", type=Path, required=True)
    parser.add_argument("--scatter-output", type=Path)
    parser.add_argument("--facet-output", type=Path)
    parser.add_argument("--accuracy-output", type=Path)
    parser.add_argument("--relative-percent", action="store_true")
    parser.add_argument("--color-mode", type=str, default="model", choices=("model", "grayscale"))
    parser.add_argument("--shade-arch-background", action="store_true")
    parser.add_argument("--arch-background-alpha", type=float, default=0.27)
    parser.add_argument("--arch-boundary-color", type=str, default="#607B8F")
    parser.add_argument("--no-arch-boundary", action="store_true")
    parser.add_argument("--title", type=str)
    parser.add_argument("--no-title", action="store_true")
    parser.add_argument("--skip-crowded-labels", action="store_true")
    parser.add_argument("--label-distance-threshold", type=float, default=0.8)
    parser.add_argument("--arrow-distance-threshold", type=float, default=0.08)
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

    with_metrics = _load_metrics(
        args.with_config,
        operations=args.operations,
        pricing_file=pricing,
        price_per_1m=args.price_per_1m,
    )
    without_metrics = _load_metrics(
        args.without_config,
        operations=args.operations,
        pricing_file=pricing,
        price_per_1m=args.price_per_1m,
    )

    plot_tavily_diff(
        with_metrics,
        without_metrics,
        scatter_output=args.scatter_output,
        facet_output=args.facet_output,
        accuracy_output=args.accuracy_output,
        relative_percent=args.relative_percent,
        color_mode=args.color_mode,
        shade_arch_background=args.shade_arch_background,
        arch_background_alpha=args.arch_background_alpha,
        arch_boundary_color=args.arch_boundary_color,
        no_arch_boundary=args.no_arch_boundary,
        title=None if args.no_title else args.title,
        skip_crowded_labels=args.skip_crowded_labels,
        label_distance_threshold=args.label_distance_threshold,
        arrow_distance_threshold=args.arrow_distance_threshold,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
