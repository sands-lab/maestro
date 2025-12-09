"""Command-line Tree of Thoughts benchmark derived from the tot.ipynb tutorial."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import operator
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Dict, List, Literal, NamedTuple, Optional, Sequence, Union

import requests
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from typing_extensions import Annotated, TypedDict

APP_NAME = "tree_of_thoughts"
BENCHMARK_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = BENCHMARK_ROOT / "data" / "game_of_24_sample.csv"
DEFAULT_DATASET_URL = (
    "https://storage.googleapis.com/benchmarks-artifacts/game-of-24/24.csv"
)
LOG_DIR = BENCHMARK_ROOT / "logs"
METADATA_VERSION = 1
TRACE_SERVICE_NAME = "tree-of-thoughts-benchmark"

logger = logging.getLogger("tree-of-thoughts-benchmark")


OperatorType = Literal["+", "-", "*", "/"]
TokenType = Union[float, OperatorType]


class FileSpanExporter(SpanExporter):
    """Writes OpenTelemetry spans to a JSONL file."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def export(self, spans) -> SpanExportResult:
        lines: List[str] = []
        for span in spans:
            context = span.context
            parent_context = span.parent
            parent_id: str | None = None
            if parent_context and getattr(parent_context, "span_id", 0):
                parent_id = format(parent_context.span_id, "016x")
            data: Dict[str, object] = {
                "name": span.name,
                "context": {
                    "trace_id": format(context.trace_id, "032x"),
                    "span_id": format(context.span_id, "016x"),
                },
                "kind": span.kind.name,
                "start_time": span.start_time,
                "end_time": span.end_time,
                "status": span.status.status_code.name,
                "attributes": dict(span.attributes),
            }
            if parent_id:
                data["parent_span_id"] = parent_id
            description = span.status.description
            if description:
                data["status_description"] = description
            lines.append(json.dumps(data))
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to clean up
        return None


def setup_tracer() -> tuple[trace.Tracer, Path, TracerProvider, str]:
    """Configure OpenTelemetry tracing and return the tracer, log path, and run id."""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    trace_path = LOG_DIR / f"run_{timestamp}.otel.jsonl"
    trace_path.touch(exist_ok=True)
    resource = Resource.create({"service.name": TRACE_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = FileSpanExporter(trace_path)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("tree-of-thoughts")
    return tracer, trace_path, provider, timestamp


class Equation(BaseModel):
    """Equation represented in reverse-polish notation."""

    tokens: List[TokenType] = Field(
        description=(
            "Reverse-polish notation tokens. Example: [3, 4, '+', -1, '*'] "
            "evaluates to (3 + 4) * -1 = -7."
        ),
    )

    def compute(self) -> float:
        op_funcs = {
            "+": operator.add,
            "-": operator.sub,
            "*": operator.mul,
            "/": operator.truediv,
        }
        stack: List[float] = []
        for token in self.tokens:
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


class GuessEquations(BaseModel):
    """Structured output schema returned by the LLM."""

    reasoning: str = Field(
        description="Explanation of the submitted guesses and thought process."
    )
    equations: List[Equation] = Field(
        description="List of candidate equations for this search iteration."
    )


class Candidate(NamedTuple):
    candidate: Equation
    score: Optional[float] = None
    feedback: Optional[str] = None

    def __str__(self) -> str:
        try:
            computed = self.candidate.compute()
        except Exception as exc:  # pragma: no cover - diagnostic only
            computed = f"Invalid equation: {exc}"
        reward = f"{self.score:.3f}" if self.score is not None else "?"
        return f"Equation({self.candidate.tokens}) = {computed} (score={reward})"


class ScoredCandidate(Candidate):
    candidate: Equation
    score: float
    feedback: str


class LLMTokenUsageCallback(BaseCallbackHandler):
    """Capture token statistics for a single LangChain LLM call."""

    def __init__(self) -> None:
        super().__init__()
        self.token_usage: Dict[str, int] | None = None

    def on_llm_end(self, response, **kwargs):  # type: ignore[override]
        usage = None
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        if isinstance(usage, dict):
            self.token_usage = {
                key: int(value)
                for key, value in usage.items()
                if isinstance(value, (int, float))
            }


def _preview_candidates(
    items: Sequence[Candidate | ScoredCandidate], limit: int = 3
) -> List[str]:
    preview = [str(item) for item in items[:limit]]
    if len(items) > limit:
        preview.append("...")
    return preview


def update_candidates(
    existing: Optional[list] = None,
    updates: Optional[Union[list, Literal["clear"]]] = None,
) -> List[str]:
    if existing is None:
        existing = []
    if updates is None:
        return existing
    if updates == "clear":
        return []
    return existing + updates


class ToTState(TypedDict, total=False):
    problem: str
    candidates: Annotated[List[Candidate], update_candidates]
    scored_candidates: Annotated[List[ScoredCandidate], update_candidates]
    depth: Annotated[int, operator.add]


class Context(TypedDict, total=False):
    max_depth: int
    threshold: float
    k: int
    beam_size: int


class EnsuredContext(TypedDict):
    max_depth: int
    threshold: float
    k: int
    beam_size: int


class ExpansionState(ToTState, total=False):
    seed: Optional[Candidate]


def _ensure_context(ctx: Context) -> EnsuredContext:
    return {
        "max_depth": ctx.get("max_depth", 10),
        "threshold": ctx.get("threshold", 0.9),
        "k": ctx.get("k", 5),
        "beam_size": ctx.get("beam_size", 3),
    }


def compute_score(problem: str, candidate: Candidate) -> ScoredCandidate:
    numbers = list(map(int, problem.split()))
    used_numbers = [
        int(token)
        for token in candidate.candidate.tokens
        if isinstance(token, float)
    ]
    if sorted(used_numbers) != sorted(numbers):
        return ScoredCandidate(
            candidate=candidate.candidate,
            score=0.0,
            feedback="Each number must be used exactly once.",
        )
    try:
        result = candidate.candidate.compute()
        score = 1 / (1 + abs(24 - result))
        feedback = f"Result: {result}"
    except Exception as exc:
        score = 0.0
        feedback = f"Invalid equation: {exc}"
    return ScoredCandidate(
        candidate=candidate.candidate,
        score=score,
        feedback=feedback,
    )


def _format_equation(tokens: Sequence[TokenType]) -> str:
    def _fmt(token: TokenType) -> str:
        if isinstance(token, float):
            return str(int(token)) if token.is_integer() else f"{token:.2f}"
        return token

    return " ".join(_fmt(token) for token in tokens)


def _summarize_state(value: object) -> object:
    if isinstance(value, dict):
        return {k: _summarize_state(v) for k, v in value.items()}
    if isinstance(value, list):
        preview = [_summarize_state(v) for v in value[:3]]
        if len(value) > 3:
            preview.append("...")
        return preview
    if isinstance(value, Candidate):
        return str(value)
    if isinstance(value, ScoredCandidate):
        return str(value)
    return value


def _summarize_event(event: Dict[str, object]) -> str:
    parts = []
    for node, payload in event.items():
        parts.append(f"{node}: {_summarize_state(payload)}")
    return " | ".join(parts)


def build_solver(model: str, temperature: float, max_tokens: Optional[int]) -> object:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are playing the Game of 24. Using ONLY the provided numbers, "
                "create reverse-polish (postfix) equations that evaluate to 24.\n"
                "Rules:\n"
                "1. Each provided number must appear exactly once in the tokens list.\n"
                "2. Only use operators from {{+, -, *, /}}.\n"
                "3. Do not introduce any other constants or numbers.\n"
                "4. Ensure the token sequence is valid reverse-polish notation.\n"
                "Submit exactly {k} guesses for this round.",
            ),
            (
                "user",
                "Solve the 24 game for these numbers: {problem}.\n"
                "Provided numbers must be used exactly once each. "
                "Return tokens in the order they would be pushed/popped when evaluating in RPN.\n"
                "Previous best attempt:\n{candidate}",
            ),
        ]
    ).partial(candidate="")
    llm_kwargs: Dict[str, object] = {"model": model, "temperature": temperature}
    if max_tokens:
        llm_kwargs["max_tokens"] = max_tokens
    llm = ChatOpenAI(**llm_kwargs)
    return prompt | llm.with_structured_output(GuessEquations)


def _thread_id_from_runtime(runtime: Runtime[Context]) -> str | None:
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable")
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
            if thread_id is not None:
                return str(thread_id)
    return None


def build_graph(solver: object, tracer: trace.Tracer | None = None) -> StateGraph:
    node_tracer = tracer or trace.get_tracer("tree-of-thoughts")

    def _span(name: str, attributes: Dict[str, object]):
        return node_tracer.start_as_current_span(name, attributes=attributes)
    def expand(
        state: ExpansionState, *, runtime: Runtime[Context]
    ) -> Dict[str, List[Candidate]]:
        ctx = _ensure_context(runtime.context)
        seed = state.get("seed")
        candidate_str = "" if not seed else f"\n\n{seed}"
        attributes: Dict[str, object] = {
            "tot.node": "expand",
            "tot.seed_present": bool(seed),
            "tot.branching_factor": ctx["k"],
            "tot.depth": int(state.get("depth") or 0),
        }
        thread_id = _thread_id_from_runtime(runtime)
        if thread_id:
            attributes["tot.thread_id"] = thread_id
        if "problem" in state:
            attributes["tot.problem"] = state["problem"]
        with _span("tot.expand", attributes) as span:
            try:
                token_tracker = LLMTokenUsageCallback()
                equation_submission = solver.invoke(
                    {"problem": state["problem"], "candidate": candidate_str, "k": ctx["k"]},
                    config={"callbacks": [token_tracker]},
                )
            except Exception as exc:
                logger.warning("LLM expansion failed: %s", exc)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                return {"candidates": []}
            new_candidates = [
                Candidate(candidate=equation) for equation in equation_submission.equations
            ]
            span.set_attribute("tot.generated_candidates", len(new_candidates))
            span.set_attribute("tot.candidate_preview", _preview_candidates(new_candidates))
            if token_tracker.token_usage:
                usage = token_tracker.token_usage
                span.set_attribute("tot.prompt_tokens", usage.get("prompt_tokens", 0))
                span.set_attribute("tot.completion_tokens", usage.get("completion_tokens", 0))
                span.set_attribute("tot.total_tokens", usage.get("total_tokens", 0))
            return {"candidates": new_candidates}

    def score(state: ToTState) -> Dict[str, object]:
        candidates = state.get("candidates") or []
        attributes: Dict[str, object] = {
            "tot.node": "score",
            "tot.candidate_count": len(candidates),
        }
        if "problem" in state:
            attributes["tot.problem"] = state["problem"]
        with _span("tot.score", attributes) as span:
            scored = [compute_score(state["problem"], candidate) for candidate in candidates]
            best = max((candidate.score or 0.0 for candidate in scored), default=0.0)
            span.set_attribute("tot.best_candidate_score", best)
            if scored:
                span.set_attribute("tot.scored_preview", _preview_candidates(scored))
            span.set_status(Status(StatusCode.OK))
            return {"scored_candidates": scored, "candidates": "clear"}

    def prune(state: ToTState, *, runtime: Runtime[Context]) -> Dict[str, object]:
        scored_candidates = state.get("scored_candidates") or []
        beam_size = _ensure_context(runtime.context)["beam_size"]
        organized = sorted(scored_candidates, key=lambda candidate: candidate.score, reverse=True)
        pruned = organized[:beam_size]
        depth = int(state.get("depth") or 0)
        attributes: Dict[str, object] = {
            "tot.node": "prune",
            "tot.scored_count": len(scored_candidates),
            "tot.pruned_count": len(pruned),
            "tot.beam_size": beam_size,
            "tot.depth": depth,
        }
        thread_id = _thread_id_from_runtime(runtime)
        if thread_id:
            attributes["tot.thread_id"] = thread_id
        with _span("tot.prune", attributes) as span:
            if pruned:
                span.set_attribute("tot.candidate_preview", _preview_candidates(pruned))
            span.set_status(Status(StatusCode.OK))
            return {
                "candidates": pruned,
                "scored_candidates": "clear",
                "depth": depth + 1,
            }

    def should_terminate(
        state: ToTState, runtime: Runtime[Context]
    ) -> Union[Literal["__end__"], List[Send]]:
        ctx = _ensure_context(runtime.context)
        candidates = state.get("candidates") or []
        depth = int(state.get("depth") or 0)
        if not candidates:
            return "__end__"
        top_score = candidates[0].score or 0.0
        if top_score >= ctx["threshold"] or depth >= ctx["max_depth"]:
            return "__end__"
        return [
            Send("expand", {**state, "seed": candidate})
            for candidate in candidates
        ]

    builder = StateGraph(state_schema=ToTState, context_schema=Context)
    builder.add_node(expand)
    builder.add_node(score)
    builder.add_node(prune)
    builder.add_edge("expand", "score")
    builder.add_edge("score", "prune")
    builder.add_conditional_edges("prune", should_terminate, path_map=["expand", "__end__"])
    builder.add_edge("__start__", "expand")
    return builder.compile(checkpointer=InMemorySaver())


def _read_puzzles_from_text(text: str) -> List[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    puzzles: List[str] = []
    reader = csv.DictReader(StringIO("\n".join(lines)))
    if reader.fieldnames:
        lowered = {column: column.lower() for column in reader.fieldnames}
        puzzle_col = next(
            (column for column, lower in lowered.items() if lower == "puzzle"), None
        )
        if not puzzle_col:
            puzzle_col = next(
                (column for column, lower in lowered.items() if "puzzle" in lower),
                None,
            )
        if puzzle_col:
            for row in reader:
                value = row.get(puzzle_col)
                if value:
                    puzzles.append(value.strip())
            if puzzles:
                return puzzles
    reader = csv.reader(StringIO("\n".join(lines)))
    for row in reader:
        if not row:
            continue
        if len(row) >= 2:
            puzzles.append(row[1].strip())
        else:
            puzzles.append(row[0].strip())
    return puzzles


def _determine_run_status(results: List[RunResult]) -> str:
    if not results:
        return "no_puzzles"
    if any(result.error for result in results):
        return "error"
    if all(result.solved for result in results):
        return "ok"
    if any(result.solved for result in results):
        return "partial"
    return "unsolved"


def load_puzzles(
    dataset_file: Optional[Path], dataset_url: Optional[str]
) -> tuple[List[str], str]:
    if dataset_file:
        text = dataset_file.read_text(encoding="utf-8")
        return _read_puzzles_from_text(text), str(dataset_file)
    if DEFAULT_DATASET.exists():
        text = DEFAULT_DATASET.read_text(encoding="utf-8")
        return _read_puzzles_from_text(text), str(DEFAULT_DATASET)
    if dataset_url:
        response = requests.get(dataset_url, timeout=15)
        response.raise_for_status()
        return _read_puzzles_from_text(response.text), dataset_url
    raise FileNotFoundError("No dataset source available")


@dataclass
class RunResult:
    index: int
    puzzle: str
    solved: bool
    best_score: float
    depth: int
    duration: float
    equation: Optional[str]
    feedback: Optional[str]
    stream_events: List[str]
    error: Optional[str] = None

    def to_metadata(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "puzzle": self.puzzle,
            "solved": self.solved,
            "best_score": self.best_score,
            "depth": self.depth,
            "duration_seconds": self.duration,
            "equation": self.equation,
            "feedback": self.feedback,
            "error": self.error,
        }


def run_problem(
    graph: StateGraph,
    puzzle: str,
    index: int,
    ctx: EnsuredContext,
    tracer: trace.Tracer,
) -> RunResult:
    thread_id = f"tot_{index}_{int(time.time() * 1000)}"
    events: List[str] = []
    with tracer.start_as_current_span(
        "tot.problem",
        attributes={
            "tot.puzzle_index": index,
            "tot.puzzle": puzzle,
            "tot.thread_id": thread_id,
        },
    ) as span:
        start = time.perf_counter()
        try:
            for event in graph.stream(
                {"problem": puzzle},
                config={"configurable": {"thread_id": thread_id}},
                context=ctx,
            ):
                summary = _summarize_event(event)
                events.append(summary)
                print(f"  {summary}", flush=True)
                span.add_event("tot.graph_event", {"tot.summary": summary})
        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error("Graph execution failed for puzzle %s: %s", index, exc)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            return RunResult(
                index=index,
                puzzle=puzzle,
                solved=False,
                best_score=0.0,
                depth=0,
                duration=duration,
                equation=None,
                feedback=None,
                stream_events=events,
                error=str(exc),
            )
        snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
        delete_state = getattr(graph, "delete_state", None)
        if callable(delete_state):
            delete_state({"configurable": {"thread_id": thread_id}})
        duration = time.perf_counter() - start
        values = snapshot.values or {}
        depth = int(values.get("depth") or 0)
        candidates: List[ScoredCandidate] = values.get("candidates") or []
        if not candidates:
            events.append("No candidates survived pruning.")
            span.set_attribute("tot.best_score", 0.0)
            span.set_attribute("tot.depth", depth)
            span.set_attribute("tot.solved", False)
            span.set_attribute("tot.duration_seconds", duration)
            return RunResult(
                index=index,
                puzzle=puzzle,
                solved=False,
                best_score=0.0,
                depth=depth,
                duration=duration,
                equation=None,
                feedback=None,
                stream_events=events,
            )
        top = candidates[0]
        best_score = float(top.score or 0.0)
        solved = best_score >= ctx["threshold"]
        equation = _format_equation(top.candidate.tokens)
        feedback = top.feedback
        events.append(
            f"Final depth={depth}, best_score={best_score:.3f}, equation={equation}"
        )
        span.set_attribute("tot.best_score", best_score)
        span.set_attribute("tot.depth", depth)
        span.set_attribute("tot.solved", solved)
        span.set_attribute("tot.duration_seconds", duration)
        if solved:
            span.set_status(Status(StatusCode.OK))
        return RunResult(
            index=index,
            puzzle=puzzle,
            solved=solved,
            best_score=best_score,
            depth=depth,
            duration=duration,
            equation=equation,
            feedback=feedback,
            stream_events=events,
        )


def write_run_artifacts(
    results: List[RunResult],
    ctx: EnsuredContext,
    args: argparse.Namespace,
    dataset_source: str,
    run_id: str,
    trace_log_path: Path,
    status: str,
) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run_{run_id}.log"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"
    with log_path.open("w", encoding="utf-8") as handle:
        for result in results:
            result_status = "solved" if result.solved else "failed"
            handle.write(
                f"[{result.index}] {result.puzzle} -> {result_status} "
                f"(score={result.best_score:.3f}, depth={result.depth}, "
                f"duration={result.duration:.2f}s)\n"
            )
            for event in result.stream_events:
                handle.write(f"  {event}\n")
    try:
        rel_trace = os.path.relpath(trace_log_path, start=BENCHMARK_ROOT)
    except ValueError:
        rel_trace = str(trace_log_path)
    metadata = {
        "metadata_version": METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_name": APP_NAME,
        "python_version": sys.version,
        "cli_argv": sys.argv[1:],
        "dataset_source": dataset_source,
        "model": args.model,
        "temperature": args.temperature,
        "search_context": ctx,
        "status": status,
        "trace_log": rel_trace,
        "problems": [result.to_metadata() for result in results],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Wrote %s and %s", log_path.name, metadata_path.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tree-of-Thoughts Game of 24 benchmark runner."
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI Chat Completions model to use.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature forwarded to the LLM.",
    )
    parser.add_argument(
        "--problem-index",
        type=int,
        default=0,
        help="Starting puzzle index (0-based).",
    )
    parser.add_argument(
        "--num-puzzles",
        type=int,
        default=1,
        help="Number of sequential puzzles to attempt.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=10,
        help="Maximum search depth before aborting.",
    )
    parser.add_argument(
        "--beam-size",
        type=int,
        default=3,
        help="Beam width retained between iterations.",
    )
    parser.add_argument(
        "--branching-factor",
        type=int,
        default=3,
        help="How many guesses to request from the LLM per iteration.",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.9,
        help="Score threshold that marks the puzzle as solved.",
    )
    parser.add_argument(
        "--dataset-file",
        type=Path,
        help="Optional local CSV file containing puzzles to avoid downloading.",
    )
    parser.add_argument(
        "--dataset-url",
        default=DEFAULT_DATASET_URL,
        help="Fallback URL used to download the Game of 24 dataset.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Maximum tokens permitted in each model response (forwarded as OpenAI max_tokens).",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    if args.num_puzzles <= 0:
        raise ValueError("--num-puzzles must be >= 1")

    puzzles, dataset_source = load_puzzles(args.dataset_file, args.dataset_url)
    if not puzzles:
        raise RuntimeError("Dataset did not contain any puzzles")
    if args.problem_index < 0 or args.problem_index >= len(puzzles):
        raise IndexError(
            f"Starting index {args.problem_index} is outside the dataset (size {len(puzzles)})"
        )
    stop = min(len(puzzles), args.problem_index + args.num_puzzles)
    slice_with_indexes = list(enumerate(puzzles))[args.problem_index:stop]

    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    tracer, trace_log_path, provider, run_id = setup_tracer()
    solver = build_solver(args.model, args.temperature, args.max_tokens)
    graph = build_graph(solver, tracer=tracer)
    search_ctx: EnsuredContext = {
        "max_depth": args.max_depth,
        "threshold": args.score_threshold,
        "k": args.branching_factor,
        "beam_size": args.beam_size,
    }

    logger.info(
        "Running Tree of Thoughts on %s puzzle(s) [%s-%s) | model=%s | ctx=%s",
        len(slice_with_indexes),
        args.problem_index,
        stop,
        args.model,
        search_ctx,
    )

    results: List[RunResult] = []
    run_status = "unknown"
    try:
        with tracer.start_as_current_span(
            "tot.run",
            attributes={
                "tot.model": args.model,
                "tot.temperature": args.temperature,
                "tot.problem_index": args.problem_index,
                "tot.requested_puzzles": args.num_puzzles,
                "tot.dataset_source": dataset_source,
            },
        ) as run_span:
            for index, puzzle in slice_with_indexes:
                logger.info("Puzzle %s -> %s", index, puzzle)
                result = run_problem(graph, puzzle, index, search_ctx, tracer)
                logger.info(
                    "Puzzle %s %s (score=%.3f depth=%s duration=%.2fs)",
                    index,
                    "solved" if result.solved else "failed",
                    result.best_score,
                    result.depth,
                    result.duration,
                )
                results.append(result)
            run_status = _determine_run_status(results)
            run_span.set_attribute("tot.status", run_status)
            run_span.set_attribute("tot.total_runs", len(results))
            run_span.set_attribute(
                "tot.solved_runs", sum(1 for result in results if result.solved)
            )
            if run_status == "ok":
                run_span.set_status(Status(StatusCode.OK))
            elif run_status == "error":
                run_span.set_status(Status(StatusCode.ERROR, run_status))
    finally:
        provider.shutdown()

    write_run_artifacts(
        results,
        search_ctx,
        args,
        dataset_source,
        run_id,
        trace_log_path,
        run_status,
    )


if __name__ == "__main__":
    main()
