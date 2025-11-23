"""Greedy scheduling engine adapted from AGNTCY tourist scheduling example."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Tuple

from .models import Assignment, GuideOffer, TouristRequest, Window


def _window_contains(tourist_window: Window, guide_window: Window) -> bool:
    return (
        tourist_window.start <= guide_window.start
        and tourist_window.end >= guide_window.end
    )


def _has_overlap(tourist: TouristRequest, guide: GuideOffer) -> bool:
    return any(_window_contains(window, guide.available_window) for window in tourist.availability)


def _preference_score(tourist: TouristRequest, guide: GuideOffer) -> int:
    return sum(1 for pref in tourist.preferences if pref in guide.categories)


def build_schedule(
    tourists: Iterable[TouristRequest],
    guides: Iterable[GuideOffer],
    min_score: int = 0,
) -> List[Assignment]:
    """Match tourists to guides using greedy scoring."""
    tourist_list = sorted(
        list(tourists),
        key=lambda req: req.earliest_start(),
    )
    guide_list = list(guides)
    guide_capacity = {g.guide_id: g.max_group_size for g in guide_list}

    assignments: List[Assignment] = []

    for tourist in tourist_list:
        if not tourist.availability:
            continue
        best: Tuple[int, GuideOffer] | None = None

        for guide in guide_list:
            if guide_capacity[guide.guide_id] <= 0:
                continue
            if tourist.budget < guide.hourly_rate:
                continue
            if not _has_overlap(tourist, guide):
                continue
            score = _preference_score(tourist, guide)
            if score < min_score:
                continue
            if best is None or score > best[0]:
                best = (score, guide)

        if best is None:
            continue

        score, chosen = best
        duration_hours = (
            (chosen.available_window.end - chosen.available_window.start).total_seconds()
            / 3600
        )
        assignment = Assignment(
            tourist_id=tourist.tourist_id,
            guide_id=chosen.guide_id,
            time_window=chosen.available_window,
            categories=chosen.categories,
            total_cost=chosen.hourly_rate * duration_hours,
            preference_score=score,
        )
        assignments.append(assignment)
        guide_capacity[chosen.guide_id] -= 1

    return assignments

