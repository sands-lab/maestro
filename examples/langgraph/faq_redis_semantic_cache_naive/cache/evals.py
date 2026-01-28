"""Lightweight performance evaluation utilities."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict, Dict, List, Optional


@dataclass
class LLMCallRecord:
    """Stores metadata about each LLM invocation."""

    model: str
    question: str
    response: str
    latency: float


class PerfEval:
    """Simple timing helper used to mimic the notebook's evaluation flow."""

    def __init__(self) -> None:
        self.total_queries: int = 0
        self._timer_start: Optional[float] = None
        self._run_start: Optional[float] = None

        self.durations_by_label: DefaultDict[str, List[float]] = defaultdict(list)
        self.llm_calls: List[LLMCallRecord] = []

    def set_total_queries(self, count: int) -> None:
        self.total_queries = count

    def __enter__(self) -> "PerfEval":
        self._run_start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        if self._run_start is None:
            return
        elapsed = time.perf_counter() - self._run_start
        cache_hits = len(self.durations_by_label.get("cache_hit", []))
        cache_misses = len(self.durations_by_label.get("cache_miss", []))
        print("\n--- Benchmark Summary ---")
        print(f"Total elapsed time: {elapsed:.2f}s")
        print(f"Cache hits: {cache_hits} | Cache misses: {cache_misses}")
        if self.total_queries:
            hit_rate = cache_hits / self.total_queries
            print(f"Hit rate: {hit_rate:.0%}")
        for label, durations in self.durations_by_label.items():
            if durations:
                avg = sum(durations) / len(durations)
                print(f"Average {label} latency: {avg*1000:.1f} ms")

    def start(self) -> None:
        self._timer_start = time.perf_counter()

    def tick(self, label: str) -> None:
        if self._timer_start is None:
            raise RuntimeError("Call start() before tick().")
        duration = time.perf_counter() - self._timer_start
        self.durations_by_label[label].append(duration)
        self._timer_start = None

    def record_llm_call(self, model: str, question: str, response: str) -> None:
        latency = 0.0
        if self.durations_by_label.get("llm_call"):
            latency = self.durations_by_label["llm_call"][-1]
        self.llm_calls.append(
            LLMCallRecord(
                model=model,
                question=question,
                response=response,
                latency=latency,
            )
        )

    def summary(self) -> Dict[str, float]:
        """Return a summary dictionary that can be consumed by tests."""
        summary: Dict[str, float] = {}
        for label, durations in self.durations_by_label.items():
            if durations:
                summary[label] = sum(durations) / len(durations)
        return summary
