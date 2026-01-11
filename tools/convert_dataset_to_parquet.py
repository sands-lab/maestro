#!/usr/bin/env python3
"""Convert dataset metrics/traces JSONL files into consolidated Parquet files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pyarrow as pa
import pyarrow.json as pajson
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPECIAL_EXAMPLES = {"crag", "language-agent-tree-search", "plan-and-execute"}
RUN_ID_RE = re.compile(r"(20\d{6}_\d{6})")
DEFAULT_SUITE_TAG = "suite-1"
SPECIAL_SUITE_TAGS = {"suite-1", "suite-2"}
TAVILY_TAGS = {"with-tavily", "without-tavily"}
DEFAULT_PATTERNS = ["*.jsonl"]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    bucket: str
    example_name: str
    tags: List[str]


def _log_info(message: str, verbose: bool) -> None:
    if verbose:
        print(message)


def _log_warning(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _normalize_tags(tags: Iterable[str]) -> List[str]:
    seen = set()
    cleaned: List[str] = []
    for tag in tags:
        tag_value = str(tag).strip()
        if not tag_value or tag_value in seen:
            continue
        cleaned.append(tag_value)
        seen.add(tag_value)
    return cleaned


def _extract_run_id(filename: str) -> str:
    match = RUN_ID_RE.search(filename)
    return match.group(1) if match else ""


def _limit_files_per_dir(files: Iterable[Path], max_files_per_dir: int) -> List[Path]:
    unique_files = sorted({path for path in files if path.is_file()})
    if max_files_per_dir <= 0:
        return unique_files
    grouped: Dict[Path, List[Path]] = {}
    for path in unique_files:
        grouped.setdefault(path.parent, []).append(path)
    limited: List[Path] = []
    for parent in sorted(grouped):
        limited.extend(sorted(grouped[parent])[:max_files_per_dir])
    return limited


def _list_files(
    base_dir: Path,
    recursive: bool,
    patterns: Iterable[str],
    max_files_per_dir: int,
) -> List[Path]:
    if not base_dir.exists():
        return []
    candidates: List[Path] = []
    for pattern in patterns:
        if recursive:
            candidates.extend(base_dir.rglob(pattern))
        else:
            candidates.extend(base_dir.glob(pattern))
    return _limit_files_per_dir(candidates, max_files_per_dir)


def _collect_standard_example(
    example_dir: Path,
    example_name: str,
    max_files_per_dir: int,
    verbose: bool,
) -> List[SourceFile]:
    sources: List[SourceFile] = []
    tags = _normalize_tags([DEFAULT_SUITE_TAG])
    for bucket in ("metrics", "traces"):
        bucket_dir = example_dir / bucket
        files = _list_files(bucket_dir, False, DEFAULT_PATTERNS, max_files_per_dir)
        if not files:
            _log_info(f"No {bucket} files found for {example_name} in {bucket_dir}", verbose)
        for path in files:
            sources.append(
                SourceFile(
                    path=path,
                    bucket=bucket,
                    example_name=example_name,
                    tags=tags,
                )
            )
    return sources


def _tags_for_special_suite(path: Path, suite: str) -> List[str]:
    tags = [suite]
    if suite == "suite-2":
        for part in path.parts:
            if part in TAVILY_TAGS:
                tags.append(part)
                break
    return _normalize_tags(tags)


def _collect_special_example(
    example_dir: Path,
    example_name: str,
    max_files_per_dir: int,
    verbose: bool,
) -> List[SourceFile]:
    sources: List[SourceFile] = []
    for suite in sorted(SPECIAL_SUITE_TAGS):
        suite_dir = example_dir / suite
        for bucket in ("metrics", "traces"):
            bucket_dir = suite_dir / bucket
            recursive = suite == "suite-2"
            files = _list_files(bucket_dir, recursive, DEFAULT_PATTERNS, max_files_per_dir)
            if not files:
                _log_info(
                    f"No {bucket} files found for {example_name}/{suite} in {bucket_dir}",
                    verbose,
                )
            for path in files:
                tags = _tags_for_special_suite(path, suite)
                sources.append(
                    SourceFile(
                        path=path,
                        bucket=bucket,
                        example_name=example_name,
                        tags=tags,
                    )
                )
    return sources


def _collect_sources(
    dataset_root: Path,
    max_files_per_dir: int,
    verbose: bool,
) -> List[SourceFile]:
    sources: List[SourceFile] = []
    for example_dir in sorted(path for path in dataset_root.iterdir() if path.is_dir()):
        example_name = example_dir.name
        if example_name in SPECIAL_EXAMPLES:
            sources.extend(
                _collect_special_example(
                    example_dir,
                    example_name,
                    max_files_per_dir,
                    verbose,
                )
            )
        else:
            sources.extend(
                _collect_standard_example(
                    example_dir,
                    example_name,
                    max_files_per_dir,
                    verbose,
                )
            )
    return sources


def _append_metadata(table: pa.Table, metadata: Dict[str, object]) -> pa.Table:
    existing = set(table.schema.names)
    for key, value in metadata.items():
        if key in existing:
            continue
        if isinstance(value, list):
            array = pa.array([value] * table.num_rows, type=pa.list_(pa.string()))
        else:
            array = pa.array([value] * table.num_rows, type=pa.string())
        table = table.append_column(key, array)
        existing.add(key)
    return table


def _drop_empty_struct_columns(table: pa.Table, source: Path) -> pa.Table:
    to_drop = []
    for field in table.schema:
        if pa.types.is_struct(field.type) and field.type.num_fields == 0:
            to_drop.append(field.name)
    if to_drop:
        _log_warning(f"Dropping empty struct columns in {source}: {', '.join(to_drop)}")
        table = table.drop(to_drop)
    return table


def _fix_array_empty_structs(array: pa.Array | pa.ChunkedArray) -> tuple[pa.Array | pa.ChunkedArray, bool]:
    if isinstance(array, pa.ChunkedArray):
        new_chunks = []
        changed = False
        for chunk in array.chunks:
            new_chunk, chunk_changed = _fix_array_empty_structs(chunk)
            new_chunks.append(new_chunk)
            changed = changed or chunk_changed
        if not changed:
            return array, False
        return pa.chunked_array(new_chunks), True

    if pa.types.is_struct(array.type):
        if array.type.num_fields == 0:
            fixed = pa.StructArray.from_arrays(
                [pa.nulls(len(array))],
                names=["_empty"],
                mask=array.is_null(),
            )
            return fixed, True
        new_children = []
        changed = False
        for i in range(array.type.num_fields):
            child = array.field(i)
            new_child, child_changed = _fix_array_empty_structs(child)
            new_children.append(new_child)
            changed = changed or child_changed
        if not changed:
            return array, False
        fixed = pa.StructArray.from_arrays(
            new_children,
            names=[field.name for field in array.type],
            mask=array.is_null(),
        )
        return fixed, True

    if pa.types.is_list(array.type) or pa.types.is_large_list(array.type):
        new_values, changed = _fix_array_empty_structs(array.values)
        if not changed:
            return array, False
        if pa.types.is_list(array.type):
            fixed = pa.ListArray.from_arrays(array.offsets, new_values, mask=array.is_null())
        else:
            fixed = pa.LargeListArray.from_arrays(array.offsets, new_values, mask=array.is_null())
        return fixed, True

    return array, False


def _fix_empty_structs(table: pa.Table, source: Path, verbose: bool) -> pa.Table:
    new_columns = []
    changed = False
    for column in table.columns:
        new_column, column_changed = _fix_array_empty_structs(column)
        new_columns.append(new_column)
        changed = changed or column_changed
    if not changed:
        return table
    _log_info(f"Filled empty struct fields in {source}", verbose)
    return pa.Table.from_arrays(new_columns, names=table.schema.names)


def _stringify_events_attributes(table: pa.Table, source: Path, verbose: bool) -> pa.Table:
    if "events" not in table.schema.names:
        return table
    events_array = table["events"]
    if isinstance(events_array, pa.ChunkedArray):
        if len(events_array.chunks) == 0:
            return table
        events_array = events_array.combine_chunks()
    if not pa.types.is_list(events_array.type) and not pa.types.is_large_list(events_array.type):
        return table

    values = events_array.values
    if not pa.types.is_struct(values.type):
        return table
    if "attributes" not in values.type:
        return table

    attr_index = values.type.get_field_index("attributes")
    attr_field = values.type.field(attr_index)
    if pa.types.is_string(attr_field.type) or pa.types.is_large_string(attr_field.type):
        return table

    attr_values = values.field(attr_index).to_pylist()
    stringified = [
        None if item is None else item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        for item in attr_values
    ]
    string_array = pa.array(stringified, type=pa.string())

    new_children = [values.field(i) for i in range(values.type.num_fields)]
    new_children[attr_index] = string_array
    new_struct = pa.StructArray.from_arrays(
        new_children,
        names=[field.name for field in values.type],
        mask=values.is_null(),
    )

    if pa.types.is_list(events_array.type):
        new_events = pa.ListArray.from_arrays(events_array.offsets, new_struct, mask=events_array.is_null())
    else:
        new_events = pa.LargeListArray.from_arrays(events_array.offsets, new_struct, mask=events_array.is_null())

    idx = table.schema.get_field_index("events")
    table = table.set_column(idx, "events", new_events)
    _log_info(f"Stringified events[].attributes in {source}", verbose)
    return table


def _parse_indexed_key(key: str, prefix: str) -> Optional[tuple[int, list[str]]]:
    if not key.startswith(prefix + "."):
        return None
    remainder = key[len(prefix) + 1 :]
    parts = remainder.split(".")
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    return int(parts[0]), parts[1:]


def _ensure_list_index(container: list, index: int) -> dict:
    while len(container) <= index:
        container.append(None)
    if container[index] is None:
        container[index] = {}
    return container[index]


def _extract_sequence_attributes(
    attrs: dict,
) -> tuple[dict, list, list, list]:
    cleaned: dict = {}
    prompts: list = []
    completions: list = []
    functions: list = []

    for key, value in attrs.items():
        parsed = _parse_indexed_key(key, "gen_ai.prompt")
        if parsed:
            index, rest = parsed
            target = _ensure_list_index(prompts, index)
            if rest and rest[0] == "tool_calls" and len(rest) >= 3 and rest[1].isdigit():
                call_index = int(rest[1])
                field = ".".join(rest[2:])
                tool_calls = target.setdefault("tool_calls", [])
                call_target = _ensure_list_index(tool_calls, call_index)
                if field:
                    call_target[field] = value
            else:
                field = ".".join(rest)
                if field:
                    target[field] = value
            continue

        parsed = _parse_indexed_key(key, "gen_ai.completion")
        if parsed:
            index, rest = parsed
            target = _ensure_list_index(completions, index)
            if rest and rest[0] == "tool_calls" and len(rest) >= 3 and rest[1].isdigit():
                call_index = int(rest[1])
                field = ".".join(rest[2:])
                tool_calls = target.setdefault("tool_calls", [])
                call_target = _ensure_list_index(tool_calls, call_index)
                if field:
                    call_target[field] = value
            else:
                field = ".".join(rest)
                if field:
                    target[field] = value
            continue

        parsed = _parse_indexed_key(key, "llm.request.functions")
        if parsed:
            index, rest = parsed
            target = _ensure_list_index(functions, index)
            field = ".".join(rest)
            if field:
                target[field] = value
            continue

        cleaned[key] = value

    return cleaned, prompts, completions, functions


def _restructure_sequence_attributes(table: pa.Table, source: SourceFile, verbose: bool) -> pa.Table:
    if source.bucket != "traces":
        return table
    if "attributes" not in table.schema.names:
        return table

    attrs_pylist = table["attributes"].to_pylist()
    cleaned_attrs: list = []

    for attrs in attrs_pylist:
        if not isinstance(attrs, dict):
            cleaned_attrs.append(attrs)
            continue

        cleaned, prompts, completions, functions = _extract_sequence_attributes(attrs)
        if prompts:
            cleaned["gen_ai_prompt"] = prompts
        if completions:
            cleaned["gen_ai_completion"] = completions
        if functions:
            cleaned["llm_request_functions"] = functions
        cleaned_attrs.append(cleaned)

    attr_index = table.schema.get_field_index("attributes")
    table = table.set_column(attr_index, "attributes", pa.array(cleaned_attrs))

    _log_info(f"Restructured sequence attributes in {source.path}", verbose)
    return table


def _normalize_record(record: object) -> Dict[str, object]:
    if not isinstance(record, dict):
        return {"_value": record}
    payload = record.get("payload")
    if payload is not None and not isinstance(payload, str):
        record["payload"] = json.dumps(payload, ensure_ascii=False)
    return record


def _read_json_fallback(path: Path) -> Optional[pa.Table]:
    records: List[Dict[str, object]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    _log_warning(f"Skipping invalid JSON line in {path}: {exc}")
                    continue
                if isinstance(data, list):
                    records.extend(_normalize_record(item) for item in data)
                else:
                    records.append(_normalize_record(data))
    except OSError as exc:
        _log_warning(f"Failed to read {path}: {exc}")
        return None
    if not records:
        return None
    return pa.Table.from_pylist(records)


def _read_table(source: SourceFile, verbose: bool) -> Optional[pa.Table]:
    try:
        table = pajson.read_json(source.path)
    except Exception as exc:  # pragma: no cover - input dependent
        _log_warning(f"Failed to read {source.path}: {exc}")
        _log_warning(f"Falling back to python JSON loader for {source.path}")
        table = _read_json_fallback(source.path)
        if table is None:
            return None

    table = _drop_empty_struct_columns(table, source.path)
    table = _fix_empty_structs(table, source.path, verbose)
    table = _stringify_events_attributes(table, source.path, verbose)
    table = _restructure_sequence_attributes(table, source, verbose)
    metadata = {
        "run_id": _extract_run_id(source.path.name),
        "example_name": source.example_name,
        "tags": source.tags,
    }
    table = _append_metadata(table, metadata)
    return table


def _align_table_to_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    for field in schema:
        if field.name not in table.schema.names:
            table = table.append_column(field.name, pa.nulls(table.num_rows, type=field.type))
    table = table.select(schema.names)
    return table.cast(schema, safe=False)


def _write_group(
    sources: Iterable[SourceFile],
    output_path: Path,
    compression: str,
    overwrite: bool,
    verbose: bool,
    progress: bool,
    progress_every: int,
    label: str,
    partition_by_example: bool,
    row_group_by_example: bool,
    row_group_max_bytes: int,
) -> None:
    source_list = list(sources)
    if not source_list:
        return
    if output_path.exists() and not overwrite:
        _log_info(f"Skipping (exists): {output_path}", verbose)
        return
    if output_path.exists() and overwrite and partition_by_example:
        shutil.rmtree(output_path)

    grouped_sources: Dict[str, List[SourceFile]] | None = None
    if row_group_by_example:
        grouped_sources = {}
        for source in source_list:
            grouped_sources.setdefault(source.example_name, []).append(source)
        ordered_sources: List[SourceFile] = []
        for example_name in sorted(grouped_sources):
            ordered_sources.extend(
                sorted(grouped_sources[example_name], key=lambda item: str(item.path))
            )
    else:
        ordered_sources = source_list

    schemas = []
    total_sources = len(ordered_sources)
    for idx, source in enumerate(ordered_sources, 1):
        table = _read_table(source, verbose)
        if table is None:
            continue
        schemas.append(table.schema)
        if progress and (idx % progress_every == 0 or idx == total_sources):
            percent = idx / total_sources * 100
            print(f"{label} schema pass: {idx}/{total_sources} ({percent:.1f}%)")

    if not schemas:
        _log_warning(f"Skipping empty group for {output_path}")
        return

    unified_schema = schemas[0] if len(schemas) == 1 else pa.unify_schemas(schemas)

    if not partition_by_example:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with pq.ParquetWriter(output_path, unified_schema, compression=compression) as writer:
            if row_group_by_example and grouped_sources is not None:
                processed = 0
                for example_name in sorted(grouped_sources):
                    example_tables: list[pa.Table] = []
                    for source in sorted(grouped_sources[example_name], key=lambda item: str(item.path)):
                        table = _read_table(source, verbose)
                        processed += 1
                        if table is None:
                            continue
                        table = _align_table_to_schema(table, unified_schema)
                        example_tables.append(table)
                        if progress and (
                            processed % progress_every == 0 or processed == total_sources
                        ):
                            percent = processed / total_sources * 100
                            print(f"{label} write pass: {processed}/{total_sources} ({percent:.1f}%)")
                    if not example_tables:
                        continue
                    combined = (
                        example_tables[0]
                        if len(example_tables) == 1
                        else pa.concat_tables(example_tables)
                    )
                    if combined.num_rows:
                        if row_group_max_bytes > 0 and combined.num_rows > 0:
                            avg_row_bytes = combined.nbytes / combined.num_rows
                            rows_per_group = max(1, int(row_group_max_bytes / max(1.0, avg_row_bytes)))
                            for offset in range(0, combined.num_rows, rows_per_group):
                                chunk = combined.slice(offset, rows_per_group)
                                if chunk.num_rows:
                                    writer.write_table(chunk, row_group_size=chunk.num_rows)
                        else:
                            writer.write_table(combined, row_group_size=combined.num_rows)
                _log_info(f"Wrote {output_path}", verbose)
                return

            for idx, source in enumerate(ordered_sources, 1):
                table = _read_table(source, verbose)
                if table is None:
                    continue
                table = _align_table_to_schema(table, unified_schema)
                writer.write_table(table)
                if progress and (idx % progress_every == 0 or idx == total_sources):
                    percent = idx / total_sources * 100
                    print(f"{label} write pass: {idx}/{total_sources} ({percent:.1f}%)")
        _log_info(f"Wrote {output_path}", verbose)
        return

    output_path.mkdir(parents=True, exist_ok=True)
    writers: dict[str, pq.ParquetWriter] = {}
    try:
        for idx, source in enumerate(ordered_sources, 1):
            table = _read_table(source, verbose)
            if table is None:
                continue
            table = _align_table_to_schema(table, unified_schema)
            safe_example = source.example_name.replace("/", "_").replace("\\", "_")
            example_dir = output_path / f"example_name={safe_example}"
            example_dir.mkdir(parents=True, exist_ok=True)
            writer = writers.get(source.example_name)
            if writer is None:
                writer = pq.ParquetWriter(
                    example_dir / f"{label}.parquet",
                    unified_schema,
                    compression=compression,
                )
                writers[source.example_name] = writer
            writer.write_table(table)
            if progress and (idx % progress_every == 0 or idx == total_sources):
                percent = idx / total_sources * 100
                print(f"{label} write pass: {idx}/{total_sources} ({percent:.1f}%)")
    finally:
        for writer in writers.values():
            writer.close()

    _log_info(f"Wrote {output_path}", verbose)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=PROJECT_ROOT / "dataset",
        help="Path to the dataset root directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "parquet",
        help="Directory to write metrics.parquet and traces.parquet.",
    )
    parser.add_argument(
        "--max-files-per-dir",
        type=int,
        default=0,
        help="For quickly debug, Limit the number of files selected per directory (0 means no limit).",
    )
    parser.add_argument(
        "--compression",
        type=str,
        default="snappy",
        help="Parquet compression codec.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output parquet files if they already exist.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        default=True,
        help="Show progress while reading/writing.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5,
        help="Print progress every N files.",
    )
    parser.add_argument(
        "--partition-by-example",
        action="store_true",
        help="Write parquet partitioned by example_name under output_dir/<bucket>/example_name=...",
    )
    parser.add_argument(
        "--row-group-by-example",
        action="store_true",
        default=None,
        help="Write a single parquet per bucket with one row group per example_name (default: enabled).",
    )
    parser.add_argument(
        "--row-group-max-bytes",
        type=int,
        default=268435456,
        help="Max bytes per row group when using --row-group-by-example (0 means no limit).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    dataset_root = args.dataset_root.resolve()
    output_dir = args.output_dir.resolve()
    row_group_default = args.row_group_by_example is None
    if row_group_default:
        args.row_group_by_example = True

    if not dataset_root.exists():
        _log_warning(f"Dataset root not found: {dataset_root}")
        return 1

    sources = _collect_sources(dataset_root, args.max_files_per_dir, args.verbose)
    if not sources:
        _log_warning("No input files found under dataset root.")
        return 0

    grouped: Dict[str, List[SourceFile]] = {}
    for source in sources:
        grouped.setdefault(source.bucket, []).append(source)

    if args.partition_by_example:
        if args.row_group_by_example and not row_group_default:
            _log_warning("Ignoring --row-group-by-example because --partition-by-example was set.")
        args.row_group_by_example = False

    for bucket, bucket_sources in sorted(grouped.items()):
        if args.partition_by_example:
            output_path = output_dir / bucket
        else:
            output_path = output_dir / f"{bucket}.parquet"
        _write_group(
            sources=bucket_sources,
            output_path=output_path,
            compression=args.compression,
            overwrite=args.overwrite,
            verbose=args.verbose,
            progress=args.progress,
            progress_every=max(1, args.progress_every),
            label=bucket,
            partition_by_example=args.partition_by_example,
            row_group_by_example=args.row_group_by_example,
            row_group_max_bytes=max(0, args.row_group_max_bytes),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
