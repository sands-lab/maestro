"""Shared helpers for plotting from consolidated parquet traces."""

from __future__ import annotations

import json
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pyarrow.dataset as ds


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET_PATH = REPO_ROOT / "parquet" / "traces.parquet"
DEFAULT_ALLOWED_OPS = ("call_llm", "execute_tool", "invoke_agent")


@dataclass
class InputSpec:
    label: str
    example_name: str
    group: Optional[str] = None
    color: Optional[str] = None
    tags_all: List[str] | None = None
    tags_any: List[str] | None = None
    model: Optional[str] = None
    system: Optional[str] = None


@dataclass
class ConfigData:
    parquet_path: Path
    specs: List[InputSpec]
    group_order: List[str]
    label_order: List[str]


@dataclass
class SpanStats:
    count: int = 0
    total_duration_ns: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def update(self, tokens: Tuple[int, int, int], duration_ns: int) -> None:
        total, inp, out = tokens
        self.count += 1
        self.total_duration_ns += duration_ns
        self.total_tokens += total
        self.input_tokens += inp
        self.output_tokens += out

    def to_row(
        self,
        label: str,
        run_id: str,
        puzzle_total: int = 0,
        puzzle_failed: int = 0,
    ) -> Dict[str, object]:
        duration_seconds = self.total_duration_ns / 1e9
        avg_duration = duration_seconds / self.count if self.count else 0.0
        avg_tokens = self.total_tokens / self.count if self.count else 0.0
        avg_input_tokens = self.input_tokens / self.count if self.count else 0.0
        avg_output_tokens = self.output_tokens / self.count if self.count else 0.0
        row: Dict[str, object] = {
            "run_label": label,
            "run_id": run_id,
            "file": run_id,
            "span_count": self.count,
            "total_duration_seconds": duration_seconds,
            "avg_duration_seconds": avg_duration,
            "total_tokens": self.total_tokens,
            "avg_total_tokens": avg_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "avg_input_tokens": avg_input_tokens,
            "avg_output_tokens": avg_output_tokens,
        }
        row["task_count"] = puzzle_total
        row["failed_tasks"] = puzzle_failed
        failed_rate = puzzle_failed / puzzle_total if puzzle_total else 0.0
        row["failure_rate"] = failed_rate
        return row


@dataclass
class RunStats:
    stats: SpanStats
    tasks_total: int = 0
    tasks_failed: int = 0
    model: Optional[str] = None
    system: Optional[str] = None
    model_match: bool = False


def _normalize_example_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    normalized = raw.strip()
    lower = normalized.lower()
    if lower in {"plan and execute", "plan-and-execute", "p&e", "p-and-e"}:
        return "plan-and-execute"
    return lower.replace(" ", "-")


def _normalize_tags(value: Optional[Iterable[str]]) -> List[str]:
    if not value:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for item in value:
        tag = str(item).strip()
        if not tag or tag in seen:
            continue
        out.append(tag)
        seen.add(tag)
    return out


def _variant_to_tags(variant: Optional[str]) -> List[str]:
    if not variant:
        return []
    v = variant.strip().lower()
    if v == "with-tavily":
        return ["suite-2", "with-tavily"]
    if v == "without-tavily":
        return ["suite-2", "without-tavily"]
    return []


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))

def _resolve_parquet_path(config_path: Path, data: object) -> Path:
    if isinstance(data, dict):
        parquet_path = data.get("parquet_path")
        if parquet_path:
            return (config_path.parent / parquet_path).resolve()
        parquet_root = data.get("parquet_root")
        if parquet_root:
            root_path = Path(str(parquet_root)).expanduser()
            if not root_path.is_absolute():
                root_path = (config_path.parent / root_path).resolve()
            return (root_path / "traces.parquet").resolve()

    env_root = os.environ.get("PARQUET_ROOT")
    if env_root:
        root_path = Path(env_root).expanduser()
        return (root_path / "traces.parquet").resolve()

    flat_path = DEFAULT_PARQUET_PATH
    if flat_path.exists():
        return flat_path
    return (REPO_ROOT / "data" / "parquet" / "traces.parquet").resolve()


def load_config(config_path: Path) -> ConfigData:
    data = _load_json(config_path)
    parquet_path = _resolve_parquet_path(config_path, data)
    if isinstance(data, dict):
        entries = data.get("inputs", [])
    else:
        entries = data

    if not isinstance(entries, list):
        raise ValueError("Config file must contain a list or an object with an 'inputs' list.")

    specs: List[InputSpec] = []
    group_order: List[str] = []
    label_order: List[str] = []

    def _record_order(group: Optional[str], label: str) -> None:
        if group and group not in group_order:
            group_order.append(group)
        if label not in label_order:
            label_order.append(label)

    def _spec_from_entry(entry: Dict[str, object], group: Optional[str]) -> InputSpec:
        label = str(entry.get("label") or entry.get("model") or "run")
        example_name = _normalize_example_name(
            str(entry.get("example_name") or entry.get("task") or "")
        )
        if not example_name:
            raise ValueError(f"Config entry missing example_name/task: {entry}")
        tags_all = _normalize_tags(entry.get("tags_all"))
        tags_any = _normalize_tags(entry.get("tags_any"))
        variant_tags = _variant_to_tags(entry.get("variant") if isinstance(entry.get("variant"), str) else None)
        for tag in variant_tags:
            if tag not in tags_all:
                tags_all.append(tag)
        spec = InputSpec(
            label=label,
            example_name=example_name,
            group=str(entry.get("group") or group) if (entry.get("group") or group) else None,
            color=str(entry.get("color")) if entry.get("color") else None,
            tags_all=tags_all or None,
            tags_any=tags_any or None,
            model=str(entry.get("model")) if entry.get("model") else None,
            system=str(entry.get("system")) if entry.get("system") else None,
        )
        _record_order(spec.group, spec.label)
        return spec

    for block in entries:
        if isinstance(block, list) and len(block) == 2 and isinstance(block[0], str) and isinstance(block[1], list):
            group_name = block[0]
            if group_name not in group_order:
                group_order.append(group_name)
            for entry in block[1]:
                if not isinstance(entry, dict):
                    raise ValueError(f"Unsupported config entry type: {entry!r}")
                specs.append(_spec_from_entry(entry, group_name))
        elif isinstance(block, dict):
            specs.append(_spec_from_entry(block, None))
        else:
            raise ValueError(f"Unsupported config entry type: {block!r}")

    if not specs:
        raise ValueError("No inputs specified in config.")

    return ConfigData(parquet_path=parquet_path, specs=specs, group_order=group_order, label_order=label_order)


def load_pricing(path: Optional[Path]) -> Dict[str, Tuple[float, float]]:
    pricing: Dict[str, Tuple[float, float]] = {}
    if not path or not path.exists():
        return pricing
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return pricing
    for key, value in data.items():
        if isinstance(value, (int, float)):
            pricing[key.lower()] = (float(value), float(value))
            continue
        if isinstance(value, dict):
            inp = value.get("input_per_1m")
            out = value.get("output_per_1m")
            if inp is None and out is None:
                continue
            if inp is None:
                inp = out
            if out is None:
                out = inp
            try:
                pricing[key.lower()] = (float(inp), float(out))
            except Exception:
                continue
    return pricing


def resolve_price(
    system: Optional[str],
    model: Optional[str],
    pricing: Dict[str, Tuple[float, float]],
    fallback: Optional[float],
) -> Optional[Tuple[float, float]]:
    keys = []
    if system and model:
        keys.append(f"{system}/{model}".lower())
    if model:
        keys.append(model.lower())
    keys.append("default")
    for key in keys:
        if key in pricing:
            return pricing[key]
    if fallback is not None:
        return (fallback, fallback)
    return None


def cost_from_tokens(
    input_tokens: float,
    output_tokens: float,
    rates: Optional[Tuple[float, float]],
) -> Optional[float]:
    if rates is None:
        return None
    inp_rate, out_rate = rates
    return (input_tokens / 1_000_000 * inp_rate) + (output_tokens / 1_000_000 * out_rate)


def attach_costs(
    rows: List[Dict[str, object]],
    pricing: Dict[str, Tuple[float, float]],
    price_per_1m: Optional[float],
) -> None:
    for row in rows:
        system = row.get("gen_ai_system")
        model = row.get("gen_ai_model")
        rates = resolve_price(
            system if isinstance(system, str) else None,
            model if isinstance(model, str) else None,
            pricing,
            price_per_1m,
        )
        if rates is None:
            continue
        inp = float(row.get("input_tokens") or 0.0)
        out = float(row.get("output_tokens") or 0.0)
        cost = cost_from_tokens(inp, out, rates)
        if cost is not None:
            row["cost_total"] = cost


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _safe_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    return None


def _tags_match(tags: Optional[List[str]], tags_all: List[str], tags_any: List[str]) -> bool:
    if not tags_all and not tags_any:
        return True
    if tags is None:
        return False
    tag_set = set(tags)
    if tags_all and not all(tag in tag_set for tag in tags_all):
        return False
    if tags_any and not any(tag in tag_set for tag in tags_any):
        return False
    return True


def _model_matches(spec_model: Optional[str], run_model: Optional[str], model_match: bool) -> bool:
    if spec_model is None:
        return True
    if model_match:
        return True
    return spec_model == run_model


def _collect_runs_for_spec(
    dataset: ds.Dataset,
    spec: InputSpec,
    operations: Sequence[str],
) -> List[Dict[str, object]]:
    if not spec.example_name:
        raise ValueError("example_name is required for parquet filtering.")

    tags_all = spec.tags_all or []
    tags_any = spec.tags_any or []

    columns = [
        "run_id",
        "duration_ns",
        "start_time",
        "end_time",
        "attributes",
    ]
    if tags_all or tags_any:
        columns.append("tags")

    scanner = dataset.scanner(
        columns=columns,
        filter=ds.field("example_name") == spec.example_name,
    )

    runs: Dict[str, RunStats] = {}
    for batch in scanner.to_batches():
        col = {name: batch.column(name).to_pylist() for name in columns}
        run_ids = col["run_id"]
        for idx, run_id in enumerate(run_ids):
            if not run_id:
                continue
            attrs = col["attributes"][idx] or {}
            if tags_all or tags_any:
                if not _tags_match(col.get("tags", [None])[idx], tags_all, tags_any):
                    continue
            stats = runs.setdefault(run_id, RunStats(stats=SpanStats()))

            req_model = _safe_str(attrs.get("gen_ai.request.model"))
            resp_model = _safe_str(attrs.get("gen_ai.response.model"))
            model = req_model or resp_model
            if stats.model is None and model:
                stats.model = model
            if spec.model and model == spec.model:
                stats.model_match = True

            system = _safe_str(attrs.get("gen_ai.system"))
            if stats.system is None and system:
                stats.system = system

            judgement = attrs.get("run.judgement")
            if judgement:
                stats.tasks_total += 1
                if "correct" not in str(judgement).lower():
                    stats.tasks_failed += 1

            op_name = _safe_str(attrs.get("gen_ai.operation.name"))
            if operations and op_name not in operations:
                continue

            duration_ns = _safe_int(col["duration_ns"][idx])
            if duration_ns <= 0:
                start = _safe_int(col["start_time"][idx])
                end = _safe_int(col["end_time"][idx])
                duration_ns = max(end - start, 0)

            total_tokens = _safe_int(attrs.get("gen_ai.usage.total_tokens"))
            input_tokens = _safe_int(attrs.get("gen_ai.usage.input_tokens"))
            output_tokens = _safe_int(attrs.get("gen_ai.usage.output_tokens"))
            if total_tokens <= 0:
                total_tokens = input_tokens + output_tokens
            stats.stats.update((total_tokens, input_tokens, output_tokens), duration_ns)

    rows: List[Dict[str, object]] = []
    for run_id, stats in runs.items():
        if not _model_matches(spec.model, stats.model, stats.model_match):
            continue
        row = stats.stats.to_row(
            label=spec.label,
            run_id=run_id,
            puzzle_total=stats.tasks_total,
            puzzle_failed=stats.tasks_failed,
        )
        if spec.group:
            row["group_label"] = spec.group
        row["gen_ai_model"] = stats.model
        row["gen_ai_system"] = stats.system
        rows.append(row)

    return rows


def load_runs_from_parquet(
    config_path: Path,
    *,
    operations: Sequence[str] = DEFAULT_ALLOWED_OPS,
    pricing_file: Optional[Path] = None,
    price_per_1m: Optional[float] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, str], List[str], List[str]]:
    config = load_config(config_path)
    dataset = ds.dataset(config.parquet_path, format="parquet")

    rows: List[Dict[str, object]] = []
    label_colors: Dict[str, str] = {}
    for spec in config.specs:
        if spec.color:
            label_colors[spec.label] = spec.color
        rows.extend(_collect_runs_for_spec(dataset, spec, operations))

    pricing = load_pricing(pricing_file)
    if pricing or price_per_1m is not None:
        attach_costs(rows, pricing, price_per_1m)

    return rows, label_colors, config.group_order, config.label_order
