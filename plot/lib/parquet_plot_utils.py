"""Small plotting helpers shared across parquet telemetry scripts."""

from __future__ import annotations

from typing import List


def should_show_label(xs: List[float], ys: List[float], idx: int, threshold: float) -> bool:
    """Return True if the point at idx is sufficiently separated from others."""
    if not xs or not ys or idx < 0 or idx >= len(xs):
        return True
    x_range = max(xs) - min(xs) or 1.0
    y_range = max(ys) - min(ys) or 1.0
    best = float("inf")
    x0, y0 = xs[idx], ys[idx]
    for j, (x1, y1) in enumerate(zip(xs, ys)):
        if j == idx:
            continue
        dx = (x0 - x1) / x_range
        dy = (y0 - y1) / y_range
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < best:
            best = dist
    return best >= threshold
