"""Simplified message models inspired by AGNTCY tourist scheduling system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _format_dt(value: datetime) -> str:
    return value.isoformat()


@dataclass
class Window:
    start: datetime
    end: datetime

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Window":
        return cls(start=_parse_dt(data["start"]), end=_parse_dt(data["end"]))

    def to_dict(self) -> Dict[str, str]:
        return {"start": _format_dt(self.start), "end": _format_dt(self.end)}


@dataclass
class TouristRequest:
    tourist_id: str
    availability: List[Window]
    budget: float
    preferences: List[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TouristRequest":
        availability = [Window.from_dict(w) for w in data["availability"]]
        return cls(
            tourist_id=data["tourist_id"],
            availability=availability,
            budget=float(data["budget"]),
            preferences=list(data["preferences"]),
        )

    def earliest_start(self) -> datetime:
        return (
            min(window.start for window in self.availability)
            if self.availability
            else datetime.max
        )


@dataclass
class GuideOffer:
    guide_id: str
    categories: List[str]
    available_window: Window
    hourly_rate: float
    max_group_size: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuideOffer":
        return cls(
            guide_id=data["guide_id"],
            categories=list(data["categories"]),
            available_window=Window.from_dict(data["available_window"]),
            hourly_rate=float(data["hourly_rate"]),
            max_group_size=int(data["max_group_size"]),
        )


@dataclass
class Assignment:
    tourist_id: str
    guide_id: str
    time_window: Window
    categories: List[str]
    total_cost: float
    preference_score: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tourist_id": self.tourist_id,
            "guide_id": self.guide_id,
            "time_window": self.time_window.to_dict(),
            "categories": self.categories,
            "total_cost": self.total_cost,
            "preference_score": self.preference_score,
        }

