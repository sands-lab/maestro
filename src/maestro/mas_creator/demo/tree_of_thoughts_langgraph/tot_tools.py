"""Tools for the LangGraph Tree-of-Thoughts demo in mas_creator."""

from __future__ import annotations

import operator
from typing import Any, Literal, Union


OperatorType = Literal["+", "-", "*", "/"]
TokenType = Union[float, OperatorType]


def _coerce_token(token: Any) -> TokenType:
    """Normalize raw token values into numeric or operator tokens."""
    if isinstance(token, (int, float)):
        return float(token)
    if isinstance(token, str):
        token = token.strip()
        if token in {"+", "-", "*", "/"}:
            return token  # type: ignore[return-value]
        return float(token)
    raise ValueError(f"Unsupported token type: {type(token)!r}")


def _compute_rpn(tokens: list[TokenType]) -> float:
    """Evaluate an RPN token sequence."""
    op_funcs = {
        "+": operator.add,
        "-": operator.sub,
        "*": operator.mul,
        "/": operator.truediv,
    }
    stack: list[float] = []
    for token in tokens:
        if isinstance(token, float):
            stack.append(token)
            continue
        if len(stack) < 2:
            raise ValueError("Invalid RPN sequence")
        b, a = stack.pop(), stack.pop()
        stack.append(op_funcs[token](a, b))
    if not stack:
        raise ValueError("Equation produced no result")
    return stack[0]


def score_candidates(problem: str, candidates: list[list[Any]]) -> list[dict[str, Any]]:
    """Score candidate RPN equations for the Game of 24 objective."""
    numbers = list(map(int, problem.split()))
    results: list[dict[str, Any]] = []

    for raw_tokens in candidates:
        try:
            tokens = [_coerce_token(token) for token in raw_tokens]
            used_numbers = [int(token) for token in tokens if isinstance(token, float)]
            if sorted(used_numbers) != sorted(numbers):
                results.append(
                    {
                        "tokens": raw_tokens,
                        "score": 0.0,
                        "feedback": "Each number must be used exactly once.",
                    }
                )
                continue
            value = _compute_rpn(tokens)
            score = 1 / (1 + abs(24 - value))
            results.append(
                {
                    "tokens": raw_tokens,
                    "score": float(score),
                    "feedback": f"Result: {value}",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "tokens": raw_tokens,
                    "score": 0.0,
                    "feedback": f"Invalid equation: {exc}",
                }
            )
    return results


def prune_candidates(
    scored_candidates: list[dict[str, Any]],
    beam_size: int = 3,
    threshold: float = 0.9,
    depth: int = 0,
    max_depth: int = 10,
) -> dict[str, Any]:
    """Prune scored candidates and decide whether to terminate search."""
    ordered = sorted(
        scored_candidates,
        key=lambda candidate: float(candidate.get("score", 0.0)),
        reverse=True,
    )
    pruned = ordered[: max(1, beam_size)]
    best_score = float(pruned[0].get("score", 0.0)) if pruned else 0.0
    terminate = (not pruned) or best_score >= threshold or depth >= max_depth
    return {
        "terminate": terminate,
        "best_score": best_score,
        "next_depth": depth + 1,
        "candidates": pruned,
    }
