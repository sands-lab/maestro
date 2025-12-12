#!/usr/bin/env python3
"""HotpotQA sampling helper for the Language Agent Tree Search benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


def _load_hotpot(source: Path) -> List[Dict[str, object]]:
    if not source.exists():
        raise FileNotFoundError(f"HotpotQA JSON file not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        try:
            payload = json.load(handle)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise SystemExit(f"Unable to parse JSON from {source}: {exc}") from exc
    if not isinstance(payload, list):
        raise SystemExit(f"Expected list payload from {source}, received {type(payload)!r}")
    return payload


def _split_context(entry: Dict[str, object]) -> Tuple[List[str], List[str]]:
    supporting_titles = {
        title for title, _ in entry.get("supporting_facts", [])  # type: ignore[assignment]
    }
    gold: List[str] = []
    distractors: List[str] = []
    for title, sentences in entry.get("context", []):  # type: ignore[assignment]
        block = " ".join(sentences)
        if title in supporting_titles:
            gold.append(f"{title}: {block}")
        else:
            distractors.append(f"{title}: {block}")
    return gold, distractors


def _build_rows(
    entries: Sequence[Dict[str, object]],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for entry in entries:
        gold, distractors = _split_context(entry)
        rows.append(
            {
                "id": entry.get("_id", ""),
                "question": entry.get("question", ""),
                "answer": entry.get("answer", ""),
                "gold_context": "\n\n".join(gold),
                "distractors": "\n\n".join(distractors),
            }
        )
    return rows


def sample_hotpot(source: Path, dest: Path, sample_size: int, seed: int) -> int:
    records = _load_hotpot(source)
    if sample_size <= 0 or sample_size > len(records):
        sample_size = len(records)
    rng = random.Random(seed)
    rng.shuffle(records)
    rows = _build_rows(records[:sample_size])

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "question", "answer", "gold_context", "distractors"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample HotpotQA questions into a CSV compatible with the LATS benchmark.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/hotpot_dev_distractor_v1.json"),
        help="Path to the HotpotQA JSON dump.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("data/hotpot_dev_questions.csv"),
        help="Destination CSV file.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=200,
        help="Number of rows to sample (defaults to the entire dataset if larger).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Deterministic RNG seed for reproducible sampling.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    count = sample_hotpot(args.source, args.dest, args.sample_size, args.seed)
    print(f"Wrote {count} rows to {args.dest}")


if __name__ == "__main__":
    main()
