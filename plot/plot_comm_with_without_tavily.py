#!/usr/bin/env python3
"""Plot total communication volume by model (with vs without tool) using paper style."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Dict, List

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

_script_dir = Path(__file__).parent
_project_root = _script_dir.parent
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.data_loaders import load_traces
from .lib.extractors import extract_message_sizes


DEFAULT_EXAMPLES = (
    "crag",
    "language-agent-tree-search",
    "plan-and-execute",
)

SETTINGS = ("with-tavily", "without-tavily")

EXAMPLE_DISPLAY_NAMES = {
    "crag": "CRAG",
    "language-agent-tree-search": "LATS",
    "plan-and-execute": "Plan-and-Execute",
}

MODEL_ALIASES = {
    "gpt-5-naono": "gpt-5-nano",
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
    "communication",
    "attributes",
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


def _model_sort_key(model: str) -> tuple[int, str]:
    label = _format_model_label(model)
    order_map = {name: idx for idx, name in enumerate(MODEL_LABEL_ORDER)}
    return (order_map.get(label, len(order_map)), label)


def _format_example_label(example: str) -> str:
    return EXAMPLE_DISPLAY_NAMES.get(example, example)


def _is_embedding_model(model: str) -> bool:
    return "embedding" in model


def _should_skip_model(model: str) -> bool:
    return model == "unknown" or _is_embedding_model(model)


def _iter_model_dirs(spans_dir: Path) -> List[Path]:
    if not spans_dir.exists():
        return []
    return sorted(path for path in spans_dir.iterdir() if path.is_dir() and path.name != "v1")


def _compute_total_comm_mb(traces: List[dict]) -> float:
    sizes, _, _, _ = extract_message_sizes(traces)
    total_kb = sum(sum(values) for values in sizes.values())
    return total_kb / 1024.0


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


def _collect_example_data_parquet(
    traces_file: Path,
    example: str,
) -> Dict[str, Dict[str, List[float]]]:
    data_by_model: Dict[str, Dict[str, List[float]]] = {}

    for setting in SETTINGS:
        traces = load_traces(
            str(traces_file),
            example_name=example,
            tags=[setting],
            columns=TRACE_COLUMNS,
        )
        grouped: Dict[str, List[dict]] = defaultdict(list)
        for span in traces:
            run_id = span.get("run_id") or ""
            grouped[run_id].append(span)

        for run_id, spans in grouped.items():
            model_name = _infer_run_model(spans)
            if _should_skip_model(model_name):
                continue
            model_entry = data_by_model.setdefault(model_name, {s: [] for s in SETTINGS})
            model_entry[setting].append(_compute_total_comm_mb(spans))

        for model_name, values in data_by_model.items():
            runs = values.get(setting, [])
            if runs:
                print(f"[{example}] {model_name}/{setting} runs={len(runs)}")

    return data_by_model


def _collect_example_data_jsonl(
    root_dir: Path,
    example: str,
) -> Dict[str, Dict[str, List[float]]]:
    spans_dir = root_dir / example / "spans"
    data_by_model: Dict[str, Dict[str, List[float]]] = {}

    for model_dir in _iter_model_dirs(spans_dir):
        model_name = _normalize_model_name(model_dir.name)
        model_entry: Dict[str, List[float]] = {}
        for setting in SETTINGS:
            traces_dir = model_dir / setting
            values: List[float] = []
            if traces_dir.is_dir():
                trace_files = sorted(traces_dir.rglob("*.otel.jsonl"))
                for trace_file in trace_files:
                    print(f"  using trace file: {trace_file}")
                    traces = load_traces(str(trace_file))
                    values.append(_compute_total_comm_mb(traces))
            model_entry[setting] = values
            print(f"[{example}] {model_name}/{setting} runs={len(values)}")
        data_by_model[model_name] = model_entry

    return data_by_model


def _models_with_both_settings(
    data_by_model: Dict[str, Dict[str, List[float]]],
) -> List[str]:
    models: List[str] = []
    for model in sorted(data_by_model.keys(), key=_model_sort_key):
        if _should_skip_model(model):
            continue
        with_vals = data_by_model.get(model, {}).get("with-tavily", [])
        without_vals = data_by_model.get(model, {}).get("without-tavily", [])
        if with_vals and without_vals:
            models.append(model)
        else:
            missing = []
            if not with_vals:
                missing.append("with-tavily")
            if not without_vals:
                missing.append("without-tavily")
            if missing:
                print(f"[{model}] missing data for {', '.join(missing)}; skipping model.")
    return models


def _plot_grouped_boxplot(
    ax: plt.Axes,
    models: List[str],
    data_by_model: Dict[str, Dict[str, List[float]]],
    title: str,
    ylabel: str,
    color_with: str,
    color_without: str,
    show_legend: bool = False,
) -> None:
    if not models:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=12)
        ax.set_axis_off()
        return

    positions = np.arange(len(models))
    box_width = 0.35

    with_data = [data_by_model[model]["with-tavily"] for model in models]
    without_data = [data_by_model[model]["without-tavily"] for model in models]

    mean_props = {
        "marker": "D",
        "markerfacecolor": "black",
        "markeredgecolor": "black",
        "markersize": 5,
    }

    box_with = ax.boxplot(
        with_data,
        positions=positions - box_width / 2,
        widths=box_width,
        patch_artist=True,
        showmeans=True,
        meanprops=mean_props,
        manage_ticks=False,
    )
    box_without = ax.boxplot(
        without_data,
        positions=positions + box_width / 2,
        widths=box_width,
        patch_artist=True,
        showmeans=True,
        meanprops=mean_props,
        manage_ticks=False,
    )

    for patch in box_with["boxes"]:
        patch.set_facecolor(color_with)
        patch.set_alpha(0.35)
        patch.set_edgecolor("black")
        patch.set_linewidth(1.0)
    for patch in box_without["boxes"]:
        patch.set_facecolor(color_without)
        patch.set_alpha(0.35)
        patch.set_edgecolor("black")
        patch.set_linewidth(1.0)
    for median in box_with["medians"] + box_without["medians"]:
        median.set_color("black")
        median.set_linewidth(1.0)
    for whisker in box_with["whiskers"] + box_without["whiskers"]:
        whisker.set_color("black")
        whisker.set_linewidth(0.9)
    for cap in box_with["caps"] + box_without["caps"]:
        cap.set_color("black")
        cap.set_linewidth(0.9)

    if title:
        ax.set_title(_format_example_label(title), fontsize=13, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.set_xticks(positions)
    ax.set_xticklabels(
        [_format_model_label(model) for model in models],
        rotation=45,
        ha="right",
    )
    ax.grid(axis="y", color="#c0c0c0", linestyle="--", linewidth=0.7, alpha=0.7)
    ax.grid(axis="x", color="#e0e0e0", linestyle="--", linewidth=0.6, alpha=0.5)
    ax.margins(x=0.01)
    ax.set_facecolor("white")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(0.9)

    if show_legend:
        legend_handles = [
            Patch(facecolor=color_with, edgecolor="black", alpha=0.35, label="with-tavily"),
            Patch(facecolor=color_without, edgecolor="black", alpha=0.35, label="without-tavily"),
        ]
        ax.legend(handles=legend_handles, loc="upper left", bbox_to_anchor=(-0.04, 1.0))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot total communication volume by model (with vs without tool) for yxc-251224.",
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
        "--output-file",
        type=str,
        default="plot/figures/comparison/comm/comm_with_without.pdf",
        help="Output PDF path.",
    )
    parser.add_argument(
        "--examples",
        type=str,
        default=",".join(DEFAULT_EXAMPLES),
        help="Comma-separated example names to include.",
    )
    args = parser.parse_args()

    traces_file = _resolve_path(args.traces_file, _project_root)
    root_dir = _resolve_path(args.root_dir, _project_root)
    output_file = _resolve_path(args.output_file, _project_root)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    _apply_paper_style()

    examples = [name.strip() for name in args.examples.split(",") if name.strip()]
    if not examples:
        raise SystemExit("No examples provided.")

    data_by_example: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for example in examples:
        if traces_file.suffix.lower() == ".parquet" and traces_file.exists():
            data_by_example[example] = _collect_example_data_parquet(traces_file, example)
        else:
            if not root_dir.exists():
                raise SystemExit(f"Missing root dir: {root_dir}")
            data_by_example[example] = _collect_example_data_jsonl(root_dir, example)

    # plot_width = max(7.0, len(examples) * 4.8)
    # plot_height = max(3.2, plot_width * 0.55)

    plot_width = 10
    plot_height = 3

    fig, axes = plt.subplots(
        1,
        len(examples),
        figsize=(plot_width, plot_height),
        sharey=False,
        constrained_layout=True,
    )
    if len(examples) == 1:
        axes = [axes]

    color_with = "#4c78a8"
    color_without = "#f58518"

    for col, example in enumerate(examples):
        example_data = data_by_example.get(example, {})
        models = _models_with_both_settings(example_data)
        _plot_grouped_boxplot(
            axes[col],
            models,
            example_data,
            example,
            "Total communication (MB)",
            color_with,
            color_without,
            show_legend=(col == 0),
        )

    fig.patch.set_facecolor("white")
    fig.savefig(str(output_file), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved plot to: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
