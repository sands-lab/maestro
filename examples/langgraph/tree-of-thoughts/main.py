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
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import warnings
from typing import Dict, List, Literal, NamedTuple, Optional, Sequence, Union

warnings.filterwarnings("ignore", category=FutureWarning, module=r"google(\.|$)")
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module="langchain_google_vertexai"
)

import requests
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
try:  # optional dependency for Gemini
    from langchain_google_genai import ChatGoogleGenerativeAI
except Exception:  # pragma: no cover - optional import
    ChatGoogleGenerativeAI = None  # type: ignore
try:  # optional dependency for Vertex AI
    from langchain_google_vertexai import ChatVertexAI
except Exception:  # pragma: no cover - optional import
    ChatVertexAI = None  # type: ignore
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from typing_extensions import Annotated, TypedDict

from maestro.telemetry_helpers.langgraph_otel import (
    DEFAULT_ENVIRONMENT,
    AgentFailureCategory,
    AgentRetryTrigger,
    PsutilMetricsRecorder,
    invoke_agent_span,
    record_invoke_agent_output,
    run_llm_with_span,
    set_agent_failure_attributes,
    set_agent_usefulness,
    span_id_hex,
    setup_jsonl_tracing,
)

APP_NAME = "tree_of_thoughts"
BENCHMARK_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = BENCHMARK_ROOT / "data" / "game_of_24_sample.csv"
DEFAULT_DATASET_URL = (
    "https://storage.googleapis.com/benchmarks-artifacts/game-of-24/24.csv"
)
LOG_DIR = BENCHMARK_ROOT / "logs"
METRICS_DIR = BENCHMARK_ROOT / "metrics"
METADATA_VERSION = 1
TRACE_SERVICE_NAME = "tree-of-thoughts-benchmark"
TRACE_SERVICE_VERSION = "1.0.0"
DEFAULT_METRICS_INTERVAL = float(os.getenv("TOT_METRICS_INTERVAL_SECONDS", "15") or 15.0)

logger = logging.getLogger("tree-of-thoughts-benchmark")


OperatorType = Literal["+", "-", "*", "/"]
TokenType = Union[float, OperatorType]


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


def override_value(
    existing: Optional[object] = None,
    updates: Optional[object] = None,
) -> Optional[object]:
    if updates is None:
        return existing
    if updates == "clear":
        return None
    return updates


class ToTState(TypedDict, total=False):
    problem: str
    candidates: Annotated[List[Candidate], update_candidates]
    scored_candidates: Annotated[List[ScoredCandidate], update_candidates]
    depth: Annotated[int, operator.add]
    expand_retry_attempts: Annotated[Optional[int], override_value]
    expand_previous_span_id: Annotated[Optional[str], override_value]


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


def build_solver(
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    provider: str,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
) -> object:
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
    llm: object
    if provider == "openai":
        llm_kwargs: Dict[str, object] = {"model": model, "temperature": temperature}
        if max_tokens:
            llm_kwargs["max_tokens"] = max_tokens
        llm = ChatOpenAI(**llm_kwargs)
    elif provider == "google":
        if ChatGoogleGenerativeAI is None:
            raise ImportError(
                "langchain-google-genai is required for provider=google. "
                "Install it via `pip install langchain-google-genai`."
            )
        llm_kwargs = {
            "model": model,
            "temperature": temperature,
            "convert_system_message_to_human": True,
        }
        if max_tokens:
            llm_kwargs["max_output_tokens"] = max_tokens
        llm = ChatGoogleGenerativeAI(**llm_kwargs)
    elif provider == "google-vertex":
        if ChatVertexAI is None:
            raise ImportError(
                "langchain-google-vertexai is required for provider=google-vertex. "
                "Install it via `pip install langchain-google-vertexai`."
            )
        if not vertex_project:
            raise EnvironmentError(
                "Vertex AI provider requires --vertex-project or GOOGLE_CLOUD_PROJECT."
            )
        location = vertex_location or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        llm_kwargs = {
            "project": vertex_project,
            "location": location,
            "model_name": model,
            "temperature": temperature,
        }
        if max_tokens:
            llm_kwargs["max_output_tokens"] = max_tokens
        llm = ChatVertexAI(**llm_kwargs)
    else:  # pragma: no cover - guarded by argparse choices
        raise ValueError(f"Unsupported provider: {provider}")
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


def build_graph(
    solver: object, tracer: trace.Tracer | None, model_name: str, gen_ai_system: str
) -> StateGraph:
    node_tracer = tracer or trace.get_tracer(APP_NAME)

    def expand(
        state: ExpansionState, *, runtime: Runtime[Context]
    ) -> Dict[str, List[Candidate]]:
        ctx = _ensure_context(runtime.context)
        seed = state.get("seed")
        candidate_str = "" if not seed else f"\n\n{seed}"
        depth = int(state.get("depth") or 0)
        attributes: Dict[str, object] = {
            "tot.node": "expand",
            "tot.seed_present": bool(seed),
            "tot.branching_factor": ctx["k"],
            "tot.depth": depth,
        }
        thread_id = _thread_id_from_runtime(runtime)
        if thread_id:
            attributes["tot.thread_id"] = thread_id
        if "problem" in state:
            attributes["tot.problem"] = state["problem"]
        payload_preview = {
            "problem": state.get("problem"),
            "depth": depth,
            "seed": str(seed) if seed else None,
        }
        with invoke_agent_span(
            node_tracer,
            "tot.node.expand",
            agent_name=f"{APP_NAME}.node.expand",
            payload=payload_preview,
            extra_attributes=attributes,
        ) as (node_span, _):
            retry_context = None
            prior_failures = int(state.get("expand_retry_attempts") or 0)
            previous_span_id = state.get("expand_previous_span_id")
            if prior_failures:
                retry_context = {
                    "retry": {
                        "attempt_number": prior_failures + 1,
                        "trigger": AgentRetryTrigger.QUALITY,
                        "reason": "Previous expansion returned no candidates.",
                    }
                }
                if previous_span_id:
                    retry_context["retry"]["previous_span_id"] = previous_span_id

            def _invoke_solver(updated_config):
                return solver.invoke(
                    {"problem": state["problem"], "candidate": candidate_str, "k": ctx["k"]},
                    config=updated_config,
                )

            def _annotate_expand(span, submission):
                span_hex = span_id_hex(span)
                if span_hex:
                    state["expand_previous_span_id"] = span_hex
                equations = getattr(submission, "equations", None) or []
                if not equations:
                    failures = int(state.get("expand_retry_attempts") or 0)
                    set_agent_failure_attributes(
                        span,
                        category=AgentFailureCategory.QUALITY,
                        reason="LLM returned zero candidate equations.",
                    )
                    set_agent_usefulness(
                        span,
                        is_useless=True,
                        reason="No candidate equations generated.",
                    )
                    state["expand_retry_attempts"] = failures + 1
                else:
                    set_agent_usefulness(
                        span,
                        is_useless=False,
                        reason="Generated candidate equations.",
                    )
                    state["expand_retry_attempts"] = 0

            try:
                equation_submission = run_llm_with_span(
                    node_tracer,
                    "tot.call_llm.expand",
                    agent_name=f"{APP_NAME}.llm",
                    phase="expand",
                    config=None,
                    invoke_fn=_invoke_solver,
                    extra_attributes={
                        "tot.branching_factor": ctx["k"],
                        "tot.depth": depth,
                        "gen_ai.system": gen_ai_system,
                        "gen_ai.request.model": model_name,
                    },
                    agent_context=retry_context,
                    postprocess_fn=_annotate_expand,
                )
            except Exception as exc:  # run_llm_with_span already records on span
                if node_span:
                    node_span.record_exception(exc)
                    node_span.set_status(Status(StatusCode.ERROR, str(exc)))
                logger.warning("LLM expansion failed: %s", exc)
                return {"candidates": []}
            new_candidates = [
                Candidate(candidate=equation) for equation in equation_submission.equations
            ]
            if node_span:
                node_span.set_attribute("tot.generated_candidates", len(new_candidates))
                node_span.set_attribute("tot.candidate_preview", _preview_candidates(new_candidates))
            return {
                "candidates": new_candidates,
                "expand_retry_attempts": int(state.get("expand_retry_attempts") or 0),
                "expand_previous_span_id": state.get("expand_previous_span_id"),
            }

    def score(state: ToTState) -> Dict[str, object]:
        candidates = state.get("candidates") or []
        attributes: Dict[str, object] = {
            "tot.node": "score",
            "tot.candidate_count": len(candidates),
        }
        if "problem" in state:
            attributes["tot.problem"] = state["problem"]
        payload_preview = {
            "problem": state.get("problem"),
            "candidate_count": len(candidates),
        }
        with invoke_agent_span(
            node_tracer,
            "tot.node.score",
            agent_name=f"{APP_NAME}.node.score",
            payload=payload_preview,
            extra_attributes=attributes,
        ) as (span, _):
            scored = [compute_score(state["problem"], candidate) for candidate in candidates]
            best = max((candidate.score or 0.0 for candidate in scored), default=0.0)
            if span:
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
        payload_preview = {
            "depth": depth,
            "beam_size": beam_size,
            "pruned_count": len(pruned),
        }
        with invoke_agent_span(
            node_tracer,
            "tot.node.prune",
            agent_name=f"{APP_NAME}.node.prune",
            payload=payload_preview,
            extra_attributes=attributes,
        ) as (span, _):
            if span:
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
    tracer: trace.Tracer | None,
) -> RunResult:
    thread_id = f"tot_{index}_{int(time.time() * 1000)}"
    events: List[str] = []
    span_attributes = {
        "tot.puzzle_index": index,
        "tot.puzzle": puzzle,
        "tot.thread_id": thread_id,
    }
    span_context = (
        invoke_agent_span(
            tracer,
            "tot.problem",
            agent_name=f"{APP_NAME}.problem",
            payload=puzzle,
            in_process_call=True,
            extra_attributes=span_attributes,
        )
        if tracer
        else nullcontext((None, 0))
    )
    with span_context as (span, puzzle_input_bytes):
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
                if span:
                    span.add_event("tot.graph_event", {"tot.summary": summary})
        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error("Graph execution failed for puzzle %s: %s", index, exc)
            if span:
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
            if span:
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
        if span:
            span.set_attribute("tot.best_score", best_score)
            span.set_attribute("tot.depth", depth)
            span.set_attribute("tot.solved", solved)
            span.set_attribute("tot.duration_seconds", duration)
            if solved:
                span.set_status(Status(StatusCode.OK))
            if equation is not None:
                record_invoke_agent_output(span, equation, puzzle_input_bytes)
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
    trace_log_path: Optional[Path],
    metrics_log_path: Optional[Path],
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
    metadata: Dict[str, object] = {
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
        "problems": [result.to_metadata() for result in results],
    }
    if trace_log_path:
        try:
            rel_trace = os.path.relpath(trace_log_path, start=BENCHMARK_ROOT)
        except ValueError:
            rel_trace = str(trace_log_path)
        metadata["trace_log"] = rel_trace
    if metrics_log_path:
        try:
            rel_metrics = os.path.relpath(metrics_log_path, start=BENCHMARK_ROOT)
        except ValueError:
            rel_metrics = str(metrics_log_path)
        metadata["metrics_log"] = rel_metrics
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Wrote %s and %s", log_path.name, metadata_path.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tree-of-Thoughts Game of 24 benchmark runner."
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="Model name for the selected provider (default: gpt-4o-mini).",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "google", "google-vertex"],
        default="openai",
        help="LLM provider to use (default: openai).",
    )
    parser.add_argument(
        "--vertex-project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT"),
        help="Google Cloud project for Vertex AI (defaults to GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--vertex-location",
        default=os.getenv("GOOGLE_CLOUD_REGION"),
        help="Vertex AI region/location (defaults to GOOGLE_CLOUD_REGION or us-central1).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature forwarded to the selected provider.",
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
        help="Maximum tokens permitted in each model response (OpenAI max_tokens / Gemini max_output_tokens).",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=DEFAULT_METRICS_INTERVAL,
        help=(
            "Seconds between psutil samples for system metrics "
            f"(default {DEFAULT_METRICS_INTERVAL}, override via TOT_METRICS_INTERVAL_SECONDS)."
        ),
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

    if args.provider == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
    elif args.provider == "google":
        if not os.getenv("GOOGLE_API_KEY"):
            raise EnvironmentError("GOOGLE_API_KEY environment variable is not set.")
    else:
        if not (
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or os.getenv("VERTEXAI_CREDENTIALS")
        ):
            raise EnvironmentError(
                "Vertex provider requires GOOGLE_APPLICATION_CREDENTIALS pointing to a service account JSON."
            )
        if not (args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")):
            raise EnvironmentError(
                "Vertex provider requires --vertex-project or GOOGLE_CLOUD_PROJECT."
            )

    gen_ai_system = (
        "vertex_ai" if args.provider == "google-vertex" else "google"
        if args.provider == "google"
        else "openai"
    )

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tracer: Optional[trace.Tracer] = None
    trace_log_path: Optional[Path] = None
    trace_provider = None
    metrics_recorder: Optional[PsutilMetricsRecorder] = None
    metrics_log_path: Optional[Path] = None
    try:
        tracer, trace_log_path, trace_provider = setup_jsonl_tracing(
            app_name=APP_NAME,
            service_name=TRACE_SERVICE_NAME,
            service_version=TRACE_SERVICE_VERSION,
            log_dir=LOG_DIR,
            run_id=run_id,
            environment=DEFAULT_ENVIRONMENT,
        )
        logger.info("OpenTelemetry trace log: %s", trace_log_path)
    except Exception as exc:  # pragma: no cover - tracing optional
        logger.warning("Unable to initialize OpenTelemetry tracing: %s", exc)
        tracer = None
        trace_log_path = None
        trace_provider = None
    try:
        metrics_recorder = PsutilMetricsRecorder(
            service_name=TRACE_SERVICE_NAME,
            service_version=TRACE_SERVICE_VERSION,
            run_id=run_id,
            output_dir=METRICS_DIR,
            environment=DEFAULT_ENVIRONMENT,
            scope=f"{APP_NAME}.system-metrics",
            interval_seconds=max(1.0, args.metrics_interval),
            logger=logger,
        )
        metrics_log_path = metrics_recorder.output_path
        metrics_recorder.start()
        logger.info("System metrics log: %s", metrics_log_path)
    except Exception as exc:  # pragma: no cover - metrics optional
        logger.warning("Unable to initialize system metrics recorder: %s", exc)
        metrics_recorder = None
        metrics_log_path = None
    solver = build_solver(
        args.model,
        args.temperature,
        args.max_tokens,
        args.provider,
        args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
        args.vertex_location or os.getenv("GOOGLE_CLOUD_REGION"),
    )
    graph = build_graph(
        solver, tracer=tracer, model_name=args.model, gen_ai_system=gen_ai_system
    )
    search_ctx: EnsuredContext = {
        "max_depth": args.max_depth,
        "threshold": args.score_threshold,
        "k": args.branching_factor,
        "beam_size": args.beam_size,
    }

    logger.info(
        "Running Tree of Thoughts on %s puzzle(s) [%s-%s) | model=%s | provider=%s | ctx=%s",
        len(slice_with_indexes),
        args.problem_index,
        stop,
        args.model,
        args.provider,
        search_ctx,
    )

    results: List[RunResult] = []
    run_status = "unknown"
    try:
        run_attributes = {
            "tot.model": args.model,
            "tot.temperature": args.temperature,
            "tot.problem_index": args.problem_index,
            "tot.requested_puzzles": args.num_puzzles,
            "tot.dataset_source": dataset_source,
            "tot.provider": args.provider,
        }
        run_payload = {
            "problem_index": args.problem_index,
            "num_puzzles": args.num_puzzles,
            "dataset_source": dataset_source,
        }
        run_span_cm = (
            invoke_agent_span(
                tracer,
                "tot.run",
                agent_name=f"{APP_NAME}.run",
                payload=run_payload,
                in_process_call=True,
                extra_attributes=run_attributes,
            )
            if tracer
            else nullcontext((None, 0))
        )
        with run_span_cm as (run_span, _):
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
            if run_span:
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
        if metrics_recorder:
            metrics_recorder.stop()
        if trace_provider:
            trace_provider.shutdown()

    write_run_artifacts(
        results,
        search_ctx,
        args,
        dataset_source,
        run_id,
        trace_log_path,
        metrics_log_path,
        run_status,
    )


if __name__ == "__main__":
    main()
