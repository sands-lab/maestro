"""Data loading utilities for OpenTelemetry trace and metrics files."""

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def _tags_match(record_tags: object, required_tags: Optional[Iterable[str]]) -> bool:
    if not required_tags:
        return True
    if not isinstance(record_tags, list):
        return False
    return all(tag in record_tags for tag in required_tags)


def _load_parquet_records(
    parquet_file: str,
    *,
    example_name: Optional[str],
    tags: Optional[Iterable[str]],
    columns: Optional[Iterable[str]] = None,
) -> List[Dict]:
    try:
        import pyarrow.dataset as ds
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ImportError("pyarrow is required to load parquet files") from exc

    records: List[Dict] = []
    parquet_path = Path(parquet_file)
    dataset = ds.dataset(parquet_path, format="parquet")
    filter_expr = None
    if example_name:
        filter_expr = ds.field("example_name") == example_name

    selected_columns = list(columns) if columns else None
    if tags and selected_columns is not None and "tags" not in selected_columns:
        selected_columns.append("tags")

    if hasattr(dataset, "scanner"):
        scanner = dataset.scanner(
            filter=filter_expr,
            columns=selected_columns,
        )
        batches = scanner.to_batches()
    else:
        batches = dataset.to_batches(
            filter=filter_expr,
            columns=selected_columns,
        )

    for batch in batches:
        for record in batch.to_pylist():
            if not _tags_match(record.get("tags"), tags):
                continue
            records.append(record)
    return records


def load_traces(
    trace_file: str,
    *,
    example_name: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    columns: Optional[Iterable[str]] = None,
) -> List[Dict]:
    """Load trace data from JSONL/JSON or Parquet file."""
    traces = []
    trace_path = Path(trace_file)
    suffix = trace_path.suffix.lower()
    if suffix == ".parquet":
        return _load_parquet_records(
            trace_file,
            example_name=example_name,
            tags=tags,
            columns=columns,
        )

    if suffix == ".jsonl":
        with open(trace_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    traces.append(json.loads(line))
        return traces

    if suffix == ".json":
        with open(trace_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("spans"), list):
            return data["spans"]
        raise ValueError(
            f"Unsupported JSON trace format (expected list or dict with 'spans'): {trace_file}"
        )

    raise ValueError(f"Trace file must be .jsonl, .json, or .parquet: {trace_file}")


def load_metrics(
    metrics_file: str,
    *,
    example_name: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    columns: Optional[Iterable[str]] = None,
) -> List[Dict]:
    """Load metrics data from JSONL or Parquet."""
    metrics = []
    metrics_path = Path(metrics_file)
    suffix = metrics_path.suffix.lower()
    if suffix == ".parquet":
        return _load_parquet_records(
            metrics_file,
            example_name=example_name,
            tags=tags,
            columns=columns,
        )

    if suffix != ".jsonl":
        raise ValueError(f"Metrics file must be .jsonl or .parquet: {metrics_file}")

    with open(metrics_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                # If the line is a list, flatten it
                if isinstance(data, list):
                    metrics.extend(data)
                else:
                    # Single metric object
                    metrics.append(data)
    return metrics
