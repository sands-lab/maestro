"""Tools for the LangGraph CRAG demo in mas_creator."""

from __future__ import annotations

import os
import re
from typing import Any

try:
    from langchain_community.tools.tavily_search import TavilySearchResults
except Exception:  # pragma: no cover - optional dependency
    TavilySearchResults = None  # type: ignore


SEED_DOCUMENTS: list[dict[str, str]] = [
    {
        "source": "seed://agent-memory",
        "content": (
            "Agent memory often includes short-term memory for recent turns, "
            "long-term memory for durable facts, and working memory used during reasoning."
        ),
    },
    {
        "source": "seed://memory-types",
        "content": (
            "Common memory categories are episodic memory, semantic memory, and procedural memory."
        ),
    },
    {
        "source": "seed://rag-memory",
        "content": (
            "RAG systems pair retrieval memory with generation. Retrieved context is then used to answer user questions."
        ),
    },
    {
        "source": "seed://tool-usage",
        "content": (
            "If local retrieval is weak, web search can supplement documents before final generation."
        ),
    },
]


def _normalize_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return {w for w in words if len(w) > 2}


def retrieve_documents(question: str, top_k: int = 4) -> list[dict[str, str]]:
    """Retrieve candidate documents for a question from a local seed corpus."""
    query_tokens = _normalize_tokens(question)
    if not query_tokens:
        return SEED_DOCUMENTS[: max(1, min(top_k, len(SEED_DOCUMENTS)))]

    scored: list[tuple[int, dict[str, str]]] = []
    for doc in SEED_DOCUMENTS:
        doc_tokens = _normalize_tokens(doc["content"])
        overlap = len(query_tokens.intersection(doc_tokens))
        scored.append((overlap, doc))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [doc for score, doc in scored if score > 0]
    if not selected:
        selected = [doc for _, doc in scored]
    return selected[: max(1, min(top_k, len(selected)))]


def tavily_search(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Execute Tavily search and return normalized result dictionaries."""
    api_key = os.getenv("TAVILY_API_KEY")
    if TavilySearchResults is None or not api_key:
        return [
            {
                "url": "local://tavily-unavailable",
                "content": (
                    "Tavily is unavailable in this environment. "
                    "Use local retrieved documents for generation."
                ),
            }
        ]

    tool = TavilySearchResults(max_results=max_results)
    raw: Any = tool.invoke({"query": query})
    items = raw if isinstance(raw, list) else [raw]
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(
                {
                    "url": item.get("url") or "tavily://result",
                    "content": item.get("content") or item.get("snippet") or "",
                }
            )
    return normalized
