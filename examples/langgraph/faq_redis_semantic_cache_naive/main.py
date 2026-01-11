"""Command-line benchmark that mirrors the Semantic Cache notebook."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import numpy as np
import redis
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from redisvl.extensions.cache.embeddings import EmbeddingsCache
from redisvl.extensions.cache.llm import SemanticCache
from redisvl.utils.vectorize import HFTextVectorizer
from sentence_transformers import SentenceTransformer

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from cache.config import load_openai_key
from cache.evals import PerfEval
from cache.faq_data_container import FAQDataContainer

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("semantic-cache-benchmark")
BENCHMARK_ROOT = Path(__file__).resolve().parent
LOG_DIR = BENCHMARK_ROOT / "logs"
APP_NAME = "faq_redis_semantic_cache_naive"
METADATA_VERSION = 1
METADATA_ENV_VARS = [
    "BENCHMARK_LLM_REQUESTS_PER_MIN",
    "BENCHMARK_LLM_RATE_PERIOD",
]
LLM_BACKEND = "openai"


def cosine_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine distance between vectors."""
    a_norm = np.linalg.norm(a, axis=1)
    b_norm = np.linalg.norm(b) if b.ndim == 1 else np.linalg.norm(b, axis=1)
    sim = np.dot(a, b) / (a_norm * b_norm)
    return 1 - sim


class InMemorySemanticCache:
    """Lightweight semantic cache used before the Redis integration."""

    def __init__(self, faq_df, model_name: str = "all-mpnet-base-v2") -> None:
        self.encoder = SentenceTransformer(model_name)
        self.faq_df = faq_df.copy().reset_index(drop=True)
        questions = self.faq_df["question"].tolist()
        logger.info("Encoding %s FAQ entries with %s", len(questions), model_name)
        self.faq_embeddings = self.encoder.encode(questions)

    def semantic_search(self, query: str) -> Tuple[int, float]:
        query_embedding = self.encoder.encode([query])[0]
        distances = cosine_dist(self.faq_embeddings, query_embedding)
        best_idx = int(np.argmin(distances))
        return best_idx, float(distances[best_idx])

    def check(self, query: str, distance_threshold: float) -> Optional[dict]:
        idx, distance = self.semantic_search(query)
        if distance <= distance_threshold:
            return {
                "prompt": self.faq_df.iloc[idx]["question"],
                "response": self.faq_df.iloc[idx]["answer"],
                "vector_distance": distance,
            }
        return None

    def add_entries(self, entries: Iterable[Tuple[str, str]]) -> None:
        for question, answer in entries:
            self.add(question, answer)

    def add(self, question: str, answer: str) -> None:
        logger.info("Adding '%s' to in-memory cache", question)
        new_row = {"question": question, "answer": answer}
        self.faq_df.loc[len(self.faq_df)] = new_row
        new_embedding = self.encoder.encode([question])
        self.faq_embeddings = np.vstack([self.faq_embeddings, new_embedding])


@dataclass
class BenchmarkSettings:
    redis_url: str
    distance_threshold: float
    llm_model: str
    ttl_seconds: int
    skip_redis: bool = False
    llm_rate_limit: Optional[float] = None


class FileSpanExporter(SpanExporter):
    """Writes spans to a run-specific text file."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def export(self, spans) -> SpanExportResult:
        lines = []
        for span in spans:
            data = {
                "name": span.name,
                "context": {
                    "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                },
                "start_time": span.start_time,
                "end_time": span.end_time,
                "status": span.status.status_code.name,
                "attributes": dict(span.attributes),
            }
            lines.append(json.dumps(data))

        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to clean up
        return None


def setup_tracer() -> tuple[trace.Tracer, Path, TracerProvider]:
    """Configure OpenTelemetry tracing and return the tracer + log path."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{timestamp}.log"
    log_path.touch(exist_ok=True)
    resource = Resource.create({"service.name": "semantic-cache-benchmark"})
    provider = TracerProvider(resource=resource)
    exporter = FileSpanExporter(log_path)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("semantic-cache-benchmark")
    return tracer, log_path, provider


class RateLimiter:
    """Simple sleep-based rate limiter for LLM calls."""

    def __init__(self, calls_per_minute: Optional[float]) -> None:
        if calls_per_minute and calls_per_minute > 0:
            self.min_interval = 60.0 / calls_per_minute
        else:
            self.min_interval = None
        self._last_call = 0.0

    def wait(self) -> None:
        if not self.min_interval:
            return
        now = time.monotonic()
        next_allowed = self._last_call + self.min_interval
        if next_allowed > now:
            time.sleep(next_allowed - now)
            now = time.monotonic()
        self._last_call = now


def _extract_run_id(log_path: Path) -> str:
    """Derive the run identifier from the log filename."""
    stem = log_path.stem
    if stem.startswith("run_"):
        return stem.split("run_", 1)[1]
    return stem


def _build_run_metadata(
    settings: BenchmarkSettings, run_id: str, status: str = "unknown"
) -> dict[str, object]:
    env_overrides = {
        key: os.getenv(key)
        for key in METADATA_ENV_VARS
        if os.getenv(key) is not None
    }
    return {
        "metadata_version": METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_name": APP_NAME,
        "python_version": sys.version,
        "cli_argv": sys.argv[1:],
        "redis_url": settings.redis_url,
        "distance_threshold": settings.distance_threshold,
        "llm_backend": LLM_BACKEND,
        "llm_model": settings.llm_model,
        "ttl_seconds": settings.ttl_seconds,
        "skip_redis": settings.skip_redis,
        "llm_rate_limit": settings.llm_rate_limit,
        "env_overrides": env_overrides,
        "status": status,
    }


def write_trace_metadata(log_path: Path, base_metadata: dict[str, object]) -> None:
    """Persist metadata describing the trace log that was just written."""
    if not log_path.exists():
        logger.warning("Trace log not found; skipping metadata write: %s", log_path)
        return

    metadata = dict(base_metadata)
    try:
        rel_path = os.path.relpath(log_path, start=BENCHMARK_ROOT)
    except ValueError:
        rel_path = str(log_path)
    metadata["trace_log"] = rel_path
    metadata["trace_log_basename"] = log_path.name

    metadata_path = log_path.with_suffix(f"{log_path.suffix}.metadata.json")
    with metadata_path.open("w", encoding="utf-8") as meta_file:
        json.dump(metadata, meta_file, indent=2, sort_keys=True)
    logger.info("Trace metadata written: %s", metadata_path)


class SemanticCacheBenchmark:
    """Orchestrates the flow from notebook steps to a runnable script."""

    def __init__(self, settings: BenchmarkSettings, tracer: trace.Tracer) -> None:
        self.settings = settings
        self.data = FAQDataContainer()
        self.in_memory_cache = InMemorySemanticCache(self.data.faq_df)
        self.tracer = tracer
        self.rate_limiter = RateLimiter(settings.llm_rate_limit)
        self._demo_queries = [
            "Is it possible to get a refund?",
            "I want my money back",
            "What are your business hours?",
        ]
        self._extended_entries = [
            (
                "What time do you open?",
                "We open our support lines at 9 a.m. Eastern Time every weekday.",
            ),
            (
                "Is there a phone app?",
                "The CustomerApp for Android and iOS gives you quick access to your orders and loyalty balance.",
            ),
            (
                "How can I change my payment method?",
                "Visit account settings > billing to update or replace your saved payment methods.",
            ),
        ]

    def run(self) -> None:
        with self.tracer.start_as_current_span(
            "benchmark_run",
            attributes={"skip_redis": self.settings.skip_redis},
        ):
            self._warm_up_cache()
            if self.settings.skip_redis:
                logger.info("Skipping Redis-backed cache per flag.")
                return
            redis_cache = self._bootstrap_redis_cache()
            if redis_cache is None:
                return
            self._run_llm_benchmark(redis_cache)

    def _warm_up_cache(self) -> None:
        with self.tracer.start_as_current_span("in_memory_demo"):
            logger.info("\n=== In-memory cache demo ===")
            for query in self._demo_queries:
                with self.tracer.start_as_current_span(
                    "in_memory_query", attributes={"query": query}
                ) as span:
                    result = self.in_memory_cache.check(
                        query, self.settings.distance_threshold
                    )
                    hit = bool(result)
                    span.set_attribute("cache.hit", hit)
                    if hit:
                        span.set_attribute(
                            "cache.distance", result["vector_distance"]
                        )
                        logger.info(
                            "HIT %-30s -> %s (dist=%.3f)",
                            f"'{query}'",
                            result["response"],
                            result["vector_distance"],
                        )
                    else:
                        logger.info("MISS '%s'", query)

            self.in_memory_cache.add_entries(self._extended_entries)

            logger.info("\n=== Extended cache demo ===")
            extended_queries = [
                "What time do you open?",
                "Is there a phone app?",
                "How can I change my payment method?",
            ]
            for query in extended_queries:
                with self.tracer.start_as_current_span(
                    "extended_query", attributes={"query": query}
                ) as span:
                    result = self.in_memory_cache.check(
                        query, self.settings.distance_threshold
                    )
                    if result:
                        span.set_attribute("cache.hit", True)
                        span.set_attribute("cache.distance", result["vector_distance"])
                        logger.info(
                            "HIT %-30s -> %s (dist=%.3f)",
                            f"'{query}'",
                            result["response"],
                            result["vector_distance"],
                        )

    def _bootstrap_redis_cache(self) -> Optional[SemanticCache]:
        with self.tracer.start_as_current_span("redis_bootstrap"):
            logger.info("\n=== Connecting to Redis at %s ===", self.settings.redis_url)
            try:
                client = redis.Redis.from_url(self.settings.redis_url)
                client.ping()
            except redis.ConnectionError as exc:
                logger.error("Could not connect to Redis: %s", exc)
                logger.error("Start redis-stack-server and retry.")
                return None

            vectorizer = HFTextVectorizer(
                model="redis/langcache-embed-v1",
                cache=EmbeddingsCache(redis_client=client, ttl=3600),
            )
            cache = SemanticCache(
                name="faq-cache",
                vectorizer=vectorizer,
                redis_client=client,
                distance_threshold=self.settings.distance_threshold,
            )

            logger.info("Loading %s FAQ entries into Redis cache", len(self.data.faq_df))
            for _, row in self.data.faq_df.iterrows():
                cache.store(prompt=row["question"], response=row["answer"])

            cache.set_ttl(self.settings.ttl_seconds)
            logger.info(
                "Applied TTL %s seconds to Redis cache", self.settings.ttl_seconds
            )
            return cache

    def _run_llm_benchmark(self, cache: SemanticCache) -> None:
        with self.tracer.start_as_current_span("llm_benchmark"):
            logger.info("\n=== Running LLM benchmark ===")
            load_openai_key()
            llm = ChatOpenAI(
                model=self.settings.llm_model,
                temperature=0.1,
                max_tokens=150,
            )

            test_questions = self.data.test_df["question"].tolist()
            perf_eval = PerfEval()
            perf_eval.set_total_queries(len(test_questions))

            with perf_eval:
                for question in test_questions:
                    with self.tracer.start_as_current_span(
                        "redis_cache_query", attributes={"question": question}
                    ) as span:
                        logger.info("\nQuestion: %s", question)
                        perf_eval.start()
                        cached_result = cache.check(question)
                        if cached_result:
                            span.set_attribute("cache.hit", True)
                            payload = (
                                cached_result[0]
                                if isinstance(cached_result, list)
                                else cached_result
                            )
                            span.set_attribute("cache.distance", payload["vector_distance"])
                            perf_eval.tick("cache_hit")
                            logger.info(
                                "Cache HIT dist=%.3f, prompt='%s'",
                                payload["vector_distance"],
                                payload["prompt"],
                            )
                            continue

                        span.set_attribute("cache.hit", False)
                        perf_eval.tick("cache_miss")
                        logger.info(
                            "Cache miss -> querying LLM %s", self.settings.llm_model
                        )
                        perf_eval.start()
                        self.rate_limiter.wait()
                        response = self._get_llm_response(llm, question)
                        perf_eval.tick("llm_call")
                        perf_eval.record_llm_call(
                            self.settings.llm_model, question, response
                        )
                        span.set_attribute("llm.response_length", len(response))
                        logger.info("LLM response: %s", response)
                        cache.store(prompt=question, response=response)

            cache.clear()

    @staticmethod
    def _get_llm_response(llm: ChatOpenAI, question: str) -> str:
        prompt = f"""
You are a helpful customer support assistant. Answer this customer question concisely.

Question: {question}

Provide a specific response in 1-2 sentences. If you cannot answer, offer a helpful next step.
"""
        response = llm.invoke([HumanMessage(content=prompt.strip())])
        return response.content.strip()


def parse_args(argv: Optional[Sequence[str]] = None) -> BenchmarkSettings:
    parser = argparse.ArgumentParser(description="Semantic cache benchmark runner.")
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REDIS_URL", "redis://localhost:6379"),
        help="Connection string for the Redis instance.",
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=0.3,
        help="Maximum cosine distance to qualify as a cache hit.",
    )
    parser.add_argument(
        "--llm-model",
        default="gpt-4o-mini",
        help="LLM used for cache misses.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=86400,
        help="TTL assigned to Redis cache entries.",
    )
    parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="Only run the in-memory cache demo.",
    )
    parser.add_argument(
        "--llm-rate-limit",
        type=float,
        default=None,
        help="Optional limit for LLM calls per minute (sleep-based).",
    )

    args = parser.parse_args(argv)
    env_rate_limit = os.getenv("BENCHMARK_LLM_REQUESTS_PER_MIN")
    rate_limit = args.llm_rate_limit
    if rate_limit is None and env_rate_limit:
        try:
            rate_limit = float(env_rate_limit)
        except ValueError:
            rate_limit = None

    return BenchmarkSettings(
        redis_url=args.redis_url,
        distance_threshold=args.distance_threshold,
        llm_model=args.llm_model,
        ttl_seconds=args.ttl_seconds,
        skip_redis=args.skip_redis,
        llm_rate_limit=rate_limit,
    )


def main(argv: Optional[Sequence[str]] = None) -> None:
    settings = parse_args(argv)
    tracer, log_path, provider = setup_tracer()
    logger.info("OpenTelemetry trace log: %s", log_path)
    run_id = _extract_run_id(log_path)
    benchmark = SemanticCacheBenchmark(settings, tracer)
    status = "ok"
    exit_code = 0
    try:
        benchmark.run()
    except Exception:  # pragma: no cover - surface full stack to console
        status = "failed"
        exit_code = 1
        logger.exception("Benchmark run failed")
    finally:
        provider.shutdown()
        run_metadata = _build_run_metadata(settings, run_id, status=status)
        write_trace_metadata(log_path, run_metadata)
        logger.info("Wrote trace log to %s", log_path)

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
