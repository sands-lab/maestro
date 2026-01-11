#!/usr/bin/env python3
"""Plot CPU/Memory by example for each model in a single-row 1x4 layout."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np

_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

CPU_OUTLIER_CUTOFF = 500.0

from .lib.data_loaders import load_metrics, load_traces
from .lib.extractors import extract_cpu_memory_usage


DEFAULT_EXAMPLES = (
    "crag",
    "language-agent-tree-search",
    "plan-and-execute",
)

DEFAULT_SETTINGS = (
    "with-tavily",
    "without-tavily",
)

EXAMPLE_LABELS = {
    "crag": "CRAG",
    "language-agent-tree-search": "LATS",
    "plan-and-execute": "Plan-and-Execute",
}

MODEL_ALIASES = {
    "gpt-5-naono": "gpt-5-nano",
}

MODEL_LABEL_COLORS = {
    "G5M": "#1F77B4",
    "G5N": "#6BAED6",
    "G4oM": "#9ECAE1",
    "Ge25F": "#E15759",
    "Ge25FL": "#F28E2B",
    "Ge20FL": "#EDC948",
}

MODEL_LABEL_ORDER = [
    "G5M",
    "G5N",
    "G4oM",
    "Ge25F",
    "Ge25FL",
    "Ge20FL",
]

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
    "run_id",
)

METRICS_COLUMNS = (
    "metric_name",
    "data_points",
    "resource",
    "run_id",
)

def _apply_paper_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 12,
            "axes.titlesize": 13,
            "axes.labelsize": 12.5,
            "xtick.labelsize": 11.5,
            "ytick.labelsize": 11.5,
            "legend.fontsize": 11.5,
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


def _normalize_model_name(model_name: str) -> str:
    normalized = MODEL_ALIASES.get(model_name, model_name)
    if normalized != model_name:
        print(f"Normalize model name: {model_name} -> {normalized}")
    return normalized


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


def _color_for_model(model: str, fallback: tuple) -> tuple:
    return MODEL_LABEL_COLORS.get(_format_model_label(model), fallback)


def _model_sort_key(model: str) -> tuple[int, str]:
    label = _format_model_label(model)
    order_map = {name: idx for idx, name in enumerate(MODEL_LABEL_ORDER)}
    return (order_map.get(label, len(order_map)), label)


def _is_embedding_model(model: str) -> bool:
    return "embedding" in model


def _should_skip_model(model: str) -> bool:
    return model == "unknown" or _is_embedding_model(model)


def _iter_model_dirs(metrics_dir: Path) -> List[Path]:
    if not metrics_dir.exists():
        return []
    return sorted(path for path in metrics_dir.iterdir() if path.is_dir() and path.name != "v1")


def _collect_metrics_files(metrics_dir: Path) -> List[Path]:
    if not metrics_dir.exists():
        return []
    return sorted(path for path in metrics_dir.rglob("*.jsonl") if path.is_file())


def _compute_mean_usage(metrics_files: List[Path]) -> Tuple[List[float], List[float]]:
    cpu_means: List[float] = []
    mem_means: List[float] = []
    for metrics_file in metrics_files:
        metrics = load_metrics(str(metrics_file))
        if not metrics:
            continue
        usage = extract_cpu_memory_usage(metrics)
        cpu_values = [
            v
            for _, v in usage.get("cpu", {}).get("process", [])
            if v <= CPU_OUTLIER_CUTOFF
        ]
        mem_values = [v for _, v in usage.get("memory", {}).get("process", [])]
        if cpu_values:
            cpu_means.append(float(np.mean(cpu_values)))
        if mem_values:
            mem_means.append(float(np.mean(mem_values)))
    return cpu_means, mem_means


def _extract_model_name(span: Dict) -> str:
    attrs = span.get("attributes", {}) or {}
    for key in MODEL_ATTR_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_model_name(value)
    resource = span.get("resource", {}) or {}
    resource_attrs = resource.get("attributes", {}) if isinstance(resource, dict) else {}
    value = resource_attrs.get("model.name")
    if isinstance(value, str) and value.strip():
        return _normalize_model_name(value)
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


def _compute_mean_usage_parquet(metrics: List[Dict]) -> Tuple[List[float], List[float]]:
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
            filtered = [v for _, v in values if v <= CPU_OUTLIER_CUTOFF]
            if filtered:
                cpu_total += float(np.mean(filtered))
                cpu_has = True
        if not cpu_has:
            cpu_values = [
                v
                for _, v in usage.get("cpu", {}).get("process", [])
                if v <= CPU_OUTLIER_CUTOFF
            ]
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
    return cpu_means, mem_means


def _models_for_setting(root_dir: Path, examples: List[str], setting: str) -> List[str]:
    models = set()
    for example in examples:
        metrics_dir = root_dir / example / "metrics"
        for model_dir in _iter_model_dirs(metrics_dir):
            setting_dir = model_dir / setting
            if setting_dir.is_dir() and any(setting_dir.rglob("*.jsonl")):
                models.add(_normalize_model_name(model_dir.name))
    return sorted(models, key=_model_sort_key)


def _collect_setting_data_parquet(
    metrics_file: Path,
    traces_file: Path,
    examples: List[str],
    setting: str,
) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    data_by_model: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for example in examples:
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
        run_model = {
            run_id: _infer_run_model(spans) for run_id, spans in spans_by_run.items()
        }

        metrics = load_metrics(
            str(metrics_file),
            example_name=example,
            tags=[setting],
            columns=METRICS_COLUMNS,
        )
        metrics_by_run: Dict[str, List[Dict]] = defaultdict(list)
        for metric in metrics:
            run_id = metric.get("run_id") or ""
            metrics_by_run[run_id].append(metric)

        for run_id, run_metrics in metrics_by_run.items():
            model_name = run_model.get(run_id, "unknown")
            if _should_skip_model(model_name):
                continue
            cpu_means, mem_means = _compute_mean_usage_parquet(run_metrics)
            entry = data_by_model.setdefault(model_name, {}).setdefault(
                example, {"cpu": [], "mem": []}
            )
            entry["cpu"].extend(cpu_means)
            entry["mem"].extend(mem_means)

        for model_name, values in data_by_model.items():
            cpu_runs = len(values.get(example, {}).get("cpu", []))
            mem_runs = len(values.get(example, {}).get("mem", []))
            if cpu_runs or mem_runs:
                print(
                    f"[{setting}] {model_name} / {example} "
                    f"cpu_runs={cpu_runs} mem_runs={mem_runs}"
                )
    return data_by_model


def _collect_setting_data(
    root_dir: Path,
    examples: List[str],
    setting: str,
) -> Dict[str, Dict[str, Dict[str, List[float]]]]:
    data_by_model: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for example in examples:
        metrics_dir = root_dir / example / "metrics"
        for model_dir in _iter_model_dirs(metrics_dir):
            setting_dir = model_dir / setting
            if not (setting_dir.is_dir() and any(setting_dir.rglob("*.jsonl"))):
                continue
            model_name = _normalize_model_name(model_dir.name)
            metrics_files = _collect_metrics_files(setting_dir)
            cpu_means, mem_means = _compute_mean_usage(metrics_files)
            entry = data_by_model.setdefault(model_name, {}).setdefault(
                example, {"cpu": [], "mem": []}
            )
            entry["cpu"].extend(cpu_means)
            entry["mem"].extend(mem_means)
            print(
                f"[{setting}] {model_name} / {example} "
                f"metrics_files={len(metrics_files)} cpu_runs={len(cpu_means)} mem_runs={len(mem_means)}"
            )
    return data_by_model


def _compute_bar_layout(
    num_examples: int,
    num_models: int,
    bar_width: float,
    bar_gap: float,
    group_gap: float,
) -> Tuple[Dict[Tuple[int, int], float], List[float], List[Tuple[float, float]]]:
    positions: Dict[Tuple[int, int], float] = {}
    centers: List[float] = []
    bounds: List[Tuple[float, float]] = []
    if num_models == 0:
        return positions, centers, bounds

    group_width = num_models * bar_width + (num_models - 1) * bar_gap
    for ex_idx in range(num_examples):
        group_start = ex_idx * (group_width + group_gap)
        center = group_start + (group_width - bar_width) / 2
        left = group_start - bar_width / 2
        right = group_start + (num_models - 1) * (bar_width + bar_gap) + bar_width / 2
        centers.append(center)
        bounds.append((left, right))
        for model_idx in range(num_models):
            positions[(ex_idx, model_idx)] = group_start + model_idx * (bar_width + bar_gap)
    return positions, centers, bounds


def _style_axis(ax: plt.Axes) -> None:
    ax.grid(axis="y", color="#c0c0c0", linestyle="--", linewidth=0.7, alpha=0.7)
    ax.grid(axis="x", color="#e0e0e0", linestyle="--", linewidth=0.6, alpha=0.5)
    ax.margins(x=0.01)
    ax.set_facecolor("white")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.9)


def _plot_setting_metric(
    ax: plt.Axes,
    setting_label: str,
    metric_key: str,
    ylabel: str,
    examples: List[str],
    example_labels: List[str],
    models: List[str],
    data_by_model: Dict[str, Dict[str, Dict[str, List[float]]]],
    color_map: Dict[str, tuple],
) -> None:
    if not models:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=12)
        ax.set_axis_off()
        return

    bar_width = min(0.18, 0.8 / max(1, len(models)))
    bar_gap = bar_width * 0.2
    group_gap = bar_width * 2.5
    positions, centers, bounds = _compute_bar_layout(
        len(examples), len(models), bar_width, bar_gap, group_gap
    )

    for ex_idx, example in enumerate(examples):
        model_means: List[float] = []
        for model_idx, model in enumerate(models):
            values = data_by_model.get(model, {}).get(example, {}).get(metric_key, [])
            if not values:
                continue
            mean_val = float(np.mean(values))
            std_val = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            ax.bar(
                positions[(ex_idx, model_idx)],
                mean_val,
                yerr=std_val,
                width=bar_width,
                color=color_map[model],
                edgecolor="black",
                linewidth=0.9,
                alpha=0.7,
                capsize=3,
            )
            model_means.append(mean_val)

        if model_means:
            left, right = bounds[ex_idx]
            ax.hlines(
                np.mean(model_means),
                left,
                right,
                color="#e31a1c",
                linewidth=1.2,
                linestyle="--",
            )

    ax.set_xticks(centers)
    ax.set_xticklabels(example_labels, rotation=0, ha="center")
    ax.set_ylabel(ylabel)
    ax.set_title(f"{setting_label} - {metric_key.upper()}", fontsize=13, fontweight="bold")
    ax.set_ylim(bottom=0.0)
    _style_axis(ax)


def _plot_row_layout(
    settings: List[str],
    examples: List[str],
    models_by_setting: Dict[str, List[str]],
    data_by_setting: Dict[str, Dict[str, Dict[str, Dict[str, List[float]]]]],
    output_file: Path,
) -> None:
    panels = [
        (settings[0], "cpu", "CPU (%)"),
        (settings[1], "cpu", "CPU (%)"),
        (settings[0], "mem", "Memory (MB)"),
        (settings[1], "mem", "Memory (MB)"),
    ]

    plot_width = max(18.0, len(panels) * 4.2)
    plot_height = 2.5
    fig, axes = plt.subplots(
        1,
        len(panels),
        figsize=(plot_width, plot_height),
        sharey=False,
        constrained_layout=False,
    )
    example_labels = [EXAMPLE_LABELS.get(example, example) for example in examples]

    for ax, (setting, metric_key, ylabel) in zip(axes, panels):
        models = models_by_setting.get(setting, [])
        if not models:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=12)
            ax.set_axis_off()
            continue
        cmap = plt.get_cmap("tab10")
        color_map = {
            model: _color_for_model(model, cmap(idx % 10))
            for idx, model in enumerate(models)
        }
        _plot_setting_metric(
            ax,
            setting,
            metric_key,
            ylabel,
            examples,
            example_labels,
            models,
            data_by_setting.get(setting, {}),
            color_map,
        )

    all_models = sorted(
        {model for models in models_by_setting.values() for model in models},
        key=_model_sort_key,
    )
    if all_models:
        cmap = plt.get_cmap("tab10")
        legend_handles = [
            Patch(
                facecolor=_color_for_model(model, cmap(idx % 10)),
                edgecolor="black",
                alpha=0.7,
                label=_format_model_label(model),
            )
            for idx, model in enumerate(all_models)
        ]
        legend_handles.append(
            Line2D(
                [0],
                [0],
                color="#e31a1c",
                linestyle="--",
                linewidth=1.2,
                label="mean across models per architecture",
            )
        )
        fig.legend(
            handles=legend_handles,
            loc="upper center",
            ncol=min(len(legend_handles), 8),
            frameon=False,
            bbox_to_anchor=(0.5, 1.02),
        )

    fig.patch.set_facecolor("white")
    fig.subplots_adjust(top=0.78, wspace=0.18)
    fig.savefig(str(output_file), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot to: {output_file}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot CPU/Memory by example for each model in a single-row 1x4 layout.",
    )
    parser.add_argument(
        "--metrics-file",
        type=str,
        default="data/metrics.parquet",
        help="Parquet metrics file (preferred).",
    )
    parser.add_argument(
        "--traces-file",
        type=str,
        default="data/traces.parquet",
        help="Parquet traces file (used to map runs to models).",
    )
    parser.add_argument(
        "--root-dir",
        type=str,
        default="yxc-251224",
        help="Legacy root directory containing example subfolders (JSONL layout).",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="plot/figures/comparison/cpu_mem/cpu_mem_by_example_row.pdf",
        help="Output PDF path.",
    )
    parser.add_argument(
        "--examples",
        type=str,
        default=",".join(DEFAULT_EXAMPLES),
        help="Comma-separated example names to include.",
    )
    parser.add_argument(
        "--settings",
        type=str,
        default=",".join(DEFAULT_SETTINGS),
        help="Comma-separated settings to include (with-tavily,without-tavily).",
    )
    args = parser.parse_args()

    metrics_file = _resolve_path(args.metrics_file, _project_root)
    traces_file = _resolve_path(args.traces_file, _project_root)
    root_dir = _resolve_path(args.root_dir, _project_root)
    output_file = _resolve_path(args.output_file, _project_root)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    _apply_paper_style()

    examples = [name.strip() for name in args.examples.split(",") if name.strip()]
    if not examples:
        raise SystemExit("No examples provided.")
    settings = [name.strip() for name in args.settings.split(",") if name.strip()]
    if len(settings) != 2:
        raise SystemExit("Provide exactly two settings for the row layout.")

    models_by_setting: Dict[str, List[str]] = {}
    data_by_setting: Dict[str, Dict[str, Dict[str, Dict[str, List[float]]]]] = {}
    use_parquet = metrics_file.suffix.lower() == ".parquet" and metrics_file.exists()
    if use_parquet:
        for setting in settings:
            data_by_setting[setting] = _collect_setting_data_parquet(
                metrics_file,
                traces_file,
                examples,
                setting,
            )
            models_by_setting[setting] = sorted(
                data_by_setting[setting].keys(),
                key=_model_sort_key,
            )
    else:
        for setting in settings:
            models_by_setting[setting] = _models_for_setting(root_dir, examples, setting)
            data_by_setting[setting] = _collect_setting_data(root_dir, examples, setting)

    _plot_row_layout(settings, examples, models_by_setting, data_by_setting, output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
