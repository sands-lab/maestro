"""Standalone benchmark inspired by AGNTCY's tourist scheduling system."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from opentelemetry import trace

from scheduler.analytics import summarize
from scheduler.engine import build_schedule
from scheduler.models import GuideOffer, TouristRequest
from otel import setup_tracer

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tourist-scheduler-benchmark")


@dataclass
class BenchmarkConfig:
    data_dir: Path
    min_preference_score: int
    demand_index: float
    log_dir: Path


def _load_json(path: Path) -> List[dict]:
    return json.loads(path.read_text())


def load_guides(path: Path) -> List[GuideOffer]:
    return [GuideOffer.from_dict(item) for item in _load_json(path)]


def load_tourists(path: Path) -> List[TouristRequest]:
    return [TouristRequest.from_dict(item) for item in _load_json(path)]


def apply_market_shift(guides: List[GuideOffer], demand_index: float) -> List[GuideOffer]:
    shifted: List[GuideOffer] = []
    for guide in guides:
        adjusted_rate = round(guide.hourly_rate * demand_index, 2)
        shifted.append(
            GuideOffer(
                guide_id=guide.guide_id,
                categories=guide.categories,
                available_window=guide.available_window,
                hourly_rate=adjusted_rate,
                max_group_size=guide.max_group_size,
            )
        )
    return shifted


class TouristSchedulingBenchmark:
    def __init__(self, config: BenchmarkConfig, tracer: trace.Tracer) -> None:
        self.config = config
        self.tracer = tracer

    def run(self) -> None:
        guides_path = self.config.data_dir / "guides.json"
        tourists_path = self.config.data_dir / "tourists.json"

        with self.tracer.start_as_current_span(
            "benchmark_run",
            attributes={
                "min_preference_score": self.config.min_preference_score,
                "demand_index": self.config.demand_index,
            },
        ):
            guides = self._load_guides(guides_path)
            tourists = self._load_tourists(tourists_path)
            logger.info("Loaded %s guides and %s tourists", len(guides), len(tourists))

            with self.tracer.start_as_current_span(
                "market_adjustment", attributes={"demand_index": self.config.demand_index}
            ):
                adjusted_guides = apply_market_shift(guides, self.config.demand_index)

            with self.tracer.start_as_current_span(
                "schedule_build",
                attributes={
                    "tourists": len(tourists),
                    "guides": len(adjusted_guides),
                },
            ):
                assignments = build_schedule(
                    tourists, adjusted_guides, min_score=self.config.min_preference_score
                )

            metrics = summarize(assignments, total_tourists=len(tourists))
            self._print_report(assignments, metrics)

    def _load_guides(self, path: Path) -> List[GuideOffer]:
        with self.tracer.start_as_current_span("load_guides", attributes={"path": str(path)}):
            return load_guides(path)

    def _load_tourists(self, path: Path) -> List[TouristRequest]:
        with self.tracer.start_as_current_span("load_tourists", attributes={"path": str(path)}):
            return load_tourists(path)

    def _print_report(self, assignments, metrics) -> None:
        logger.info("\n=== Schedule Summary ===")
        for assignment in assignments:
            logger.info(
                "- %s matched with %s (%s) cost $%.2f score=%s",
                assignment.tourist_id,
                assignment.guide_id,
                assignment.time_window.start.strftime("%H:%M"),
                assignment.total_cost,
                assignment.preference_score,
            )
        if not assignments:
            logger.info("No assignments created.")

        logger.info("\n=== Metrics ===")
        logger.info("Assignments: %s", metrics.assignments)
        logger.info("Average cost: $%.2f", metrics.avg_cost)
        logger.info("Avg preference score: %.2f", metrics.avg_preference_score)
        logger.info("Fill rate: %.0f%%", metrics.fill_rate * 100)


def parse_args(argv: Sequence[str] | None = None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(description="Tourist scheduling benchmark runner.")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
        help="Directory that stores guides.json and tourists.json.",
    )
    parser.add_argument(
        "--min-preference-score",
        type=int,
        default=1,
        help="Minimum number of overlapping preferences required for a match.",
    )
    parser.add_argument(
        "--demand-index",
        type=float,
        default=1.0,
        help="Multiplier applied to guide hourly rates to simulate market demand.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "logs",
        help="Directory to store OpenTelemetry span dumps.",
    )
    args = parser.parse_args(argv)
    return BenchmarkConfig(
        data_dir=args.data_dir,
        min_preference_score=args.min_preference_score,
        demand_index=args.demand_index,
        log_dir=args.log_dir,
    )


def main(argv: Sequence[str] | None = None) -> None:
    config = parse_args(argv)
    tracer, log_path, provider = setup_tracer(config.log_dir)
    logger.info("OpenTelemetry trace log: %s", log_path)
    benchmark = TouristSchedulingBenchmark(config, tracer)
    benchmark.run()
    provider.shutdown()
    logger.info("Trace log written to %s", log_path)


if __name__ == "__main__":
    main()

