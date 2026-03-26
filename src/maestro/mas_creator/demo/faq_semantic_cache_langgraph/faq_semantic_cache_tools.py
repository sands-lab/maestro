"""Tools for the LangGraph FAQ semantic cache demo in mas_creator."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import numpy as np


def cosine_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine distance between vectors."""
    a_norm = np.linalg.norm(a, axis=1)
    b_norm = np.linalg.norm(b) if b.ndim == 1 else np.linalg.norm(b, axis=1)
    sim = np.dot(a, b) / (a_norm * b_norm)
    return 1 - sim


def _faq_csv_path() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return (
        repo_root
        / "examples/langgraph/faq_redis_semantic_cache_naive/data/faq_pairs.csv"
    )


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def _load_faq_pairs() -> list[tuple[str, str]]:
    path = _faq_csv_path()
    if not path.exists():
        return [
            (
                "What is your refund policy?",
                "Refunds are available within 30 days of delivery.",
            ),
            (
                "How long does shipping take?",
                "Standard shipping arrives in 3-5 business days.",
            ),
        ]

    rows: list[tuple[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            q = (row.get("question") or "").strip()
            a = (row.get("answer") or "").strip()
            if q and a:
                rows.append((q, a))
    return rows


_BASE_ENTRIES: list[tuple[str, str]] = _load_faq_pairs()
_CACHE_ENTRIES: list[tuple[str, str]] = []


def _build_vocab(entries: list[tuple[str, str]]) -> list[str]:
    vocab = set()
    for question, _ in entries:
        vocab.update(_tokenize(question))
    return sorted(vocab)


def _vectorize(text: str, vocab: list[str]) -> np.ndarray:
    tokens = _tokenize(text)
    counts = np.zeros(len(vocab), dtype=float)
    idx = {token: i for i, token in enumerate(vocab)}
    for token in tokens:
        if token in idx:
            counts[idx[token]] += 1.0
    return counts


def _entries() -> list[tuple[str, str]]:
    return _BASE_ENTRIES + _CACHE_ENTRIES


def check_semantic_cache(query: str, distance_threshold: float = 0.3) -> dict[str, Any]:
    """Check whether the query can be answered from semantic cache."""
    entries = _entries()
    if not entries:
        return {
            "cache_hit": False,
            "prompt": "",
            "response": "",
            "vector_distance": 1.0,
        }

    vocab = _build_vocab(entries)
    matrix = np.vstack([_vectorize(question, vocab) for question, _ in entries])
    query_embedding = _vectorize(query, vocab)
    distances = cosine_dist(matrix, query_embedding)
    best_idx = int(np.argmin(distances))
    best_distance = float(distances[best_idx])
    best_question, best_answer = entries[best_idx]
    hit = best_distance <= distance_threshold
    return {
        "cache_hit": hit,
        "prompt": best_question,
        "response": best_answer if hit else "",
        "vector_distance": best_distance,
    }


def answer_faq_question(question: str) -> str:
    """Generate an FAQ answer for a cache miss using nearest known FAQ intent."""
    nearest = check_semantic_cache(question, distance_threshold=1.0)
    if nearest.get("prompt"):
        return (
            f"{nearest['response']} "
            f"(Closest FAQ intent: {nearest['prompt']})"
        )
    return "I could not find a matching FAQ answer. Please contact support@example.com."


def store_semantic_cache(question: str, answer: str) -> str:
    """Store a new question-answer pair into the semantic cache."""
    _CACHE_ENTRIES.append((question.strip(), answer.strip()))
    return f"Stored cache entry for question: {question.strip()}"
