"""Tools for the LangGraph Plan-and-Execute demo in mas_creator."""

from __future__ import annotations

import os
from typing import Any

try:
    from langchain_community.tools.tavily_search import TavilySearchResults
except Exception:  # pragma: no cover - optional dependency
    TavilySearchResults = None  # type: ignore


DEFAULT_REFERENCE_PASSAGES = (
    "Jannik Sinner won the 2024 Australian Open men's singles title. "
    "He is from San Candido, South Tyrol, Italy."
)


def reference_lookup(query: str) -> str:
    """Tool that returns dataset-provided passages instead of calling Tavily."""
    return DEFAULT_REFERENCE_PASSAGES


def tavily_search(query: str, max_results: int = 3) -> str:
    """Search the web with Tavily and return serialized search results."""
    api_key = os.getenv("TAVILY_API_KEY")
    if TavilySearchResults is None or not api_key:
        return (
            "Tavily is unavailable in this environment. "
            "Use reference_lookup for curated evidence."
        )

    tool = TavilySearchResults(max_results=max_results)
    result: Any = tool.invoke({"query": query})
    return str(result)
