"""Tools for the LangGraph Language Agent Tree Search demo in mas_creator."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

try:
    from langchain_community.tools.tavily_search import TavilySearchResults
except Exception:  # pragma: no cover - optional dependency
    TavilySearchResults = None  # type: ignore


REFERENCE_COLUMNS: list[tuple[str, str]] = [
    ("gold_context", "Gold Context"),
    ("supporting_context", "Supporting Context"),
    ("supporting_facts", "Supporting Facts"),
    ("context", "Context"),
    ("references", "References"),
    ("distractors", "Distractor Passages"),
]


def _default_questions_csv() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return (
        repo_root
        / "examples/langgraph/language-agent-tree-search/data/hotpot_dev_questions.csv"
    )


def _extract_references_from_row(row: dict[str, str]) -> str:
    blocks: list[str] = []
    for column, label in REFERENCE_COLUMNS:
        value = row.get(column)
        if value:
            text = value.strip()
            if text:
                blocks.append(f"{label}:\n{text}")
    return "\n\n".join(blocks).strip()


def reference_lookup(query: str) -> str:
    """Return dataset reference passages relevant to the query when available."""
    csv_path = _default_questions_csv()
    if not csv_path.exists():
        return "No dataset reference passages are available."

    query_lower = query.lower().strip()
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            question = (row.get("question") or "").strip()
            if not question:
                continue
            if query_lower in question.lower() or question.lower() in query_lower:
                references = _extract_references_from_row(row)
                if references:
                    return references
    return "No matching references found for this query in dataset."


def tavily_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web with Tavily and return normalized snippet results."""
    api_key = os.getenv("TAVILY_API_KEY")
    if TavilySearchResults is None or not api_key:
        return [
            {
                "url": "local://tavily-unavailable",
                "content": "Tavily is unavailable in this environment.",
            }
        ]

    tool = TavilySearchResults(max_results=max_results)
    raw: Any = tool.invoke({"query": query})
    items = raw if isinstance(raw, list) else [raw]
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "url": item.get("url") or "tavily://result",
                "content": item.get("content") or item.get("snippet") or "",
            }
        )
    return normalized
