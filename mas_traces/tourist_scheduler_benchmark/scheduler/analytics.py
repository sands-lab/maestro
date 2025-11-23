"""Utility helpers to compute benchmark metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Assignment


@dataclass
class ScheduleMetrics:
    assignments: int
    avg_cost: float
    avg_preference_score: float
    fill_rate: float

    def to_dict(self) -> dict[str, float]:
        return {
            "assignments": float(self.assignments),
            "avg_cost": self.avg_cost,
            "avg_preference_score": self.avg_preference_score,
            "fill_rate": self.fill_rate,
        }


def summarize(assignments: Iterable[Assignment], total_tourists: int) -> ScheduleMetrics:
    assignments = list(assignments)
    if not assignments:
        return ScheduleMetrics(assignments=0, avg_cost=0.0, avg_preference_score=0.0, fill_rate=0.0)

    avg_cost = sum(a.total_cost for a in assignments) / len(assignments)
    avg_score = sum(a.preference_score for a in assignments) / len(assignments)
    fill_rate = len(assignments) / total_tourists if total_tourists else 0.0

    return ScheduleMetrics(
        assignments=len(assignments),
        avg_cost=avg_cost,
        avg_preference_score=avg_score,
        fill_rate=fill_rate,
    )

