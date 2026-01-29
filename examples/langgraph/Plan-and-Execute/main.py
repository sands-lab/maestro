"""Command-line translation of the Plan-and-Execute notebook."""

import argparse
import asyncio
import csv
import json
import logging
import operator
import os
import time
import sys
import warnings
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Tuple

try:  # pragma: no cover - optional import shim
    from langchain.agents import create_agent as _create_agent

    def create_plan_agent(llm, tools, prompt):
        return _create_agent(llm, tools, system_prompt=prompt)
except ImportError:  # pragma: no cover - fall back to legacy name
    try:
        from langchain.agents import create_react_agent as _create_agent

        def create_plan_agent(llm, tools, prompt):
            return _create_agent(llm, tools, prompt=prompt)
    except ImportError:  # pragma: no cover
        from langgraph.prebuilt import create_react_agent as _create_agent  # type: ignore

        def create_plan_agent(llm, tools, prompt):
            return _create_agent(llm, tools, prompt=prompt)
try:
    from langchain.tools.base import BaseTool
except ImportError:  # pragma: no cover - older LangChain versions
    from langchain_core.tools import BaseTool  # type: ignore
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
try:  # pragma: no cover - optional dependency
    from langchain_google_vertexai import ChatVertexAI
except Exception:  # pragma: no cover
    ChatVertexAI = None  # type: ignore
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from maestro.telemetry_helpers.langgraph_otel import (
    DEFAULT_ENVIRONMENT,
    AgentFailureCategory,
    AgentRetryTrigger,
    LangChainUsageCallback,
    PsutilMetricsRecorder,
    invoke_agent_span,
    record_invoke_agent_output,
    record_run_judgement,
    record_usage_on_span,
    check_timeout,
    set_run_outcome,
    evaluate_answer,
    run_llm_with_span,
    set_agent_failure_attributes,
    set_agent_usefulness,
    span_id_hex,
    setup_jsonl_tracing,
)

warnings.filterwarnings(
    "ignore",
    message=r"The class `TavilySearchResults` was deprecated.*",
    category=DeprecationWarning,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("plan-and-execute")
BENCHMARK_ROOT = Path(__file__).resolve().parent
LOG_DIR = BENCHMARK_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
METRICS_DIR = BENCHMARK_ROOT / "metrics"
DEFAULT_OBJECTIVE = "what is the hometown of the mens 2024 Australia open winner?"
RUN_METADATA_VERSION = 1
APP_NAME = "plan_and_execute_benchmark"
TRACE_SERVICE_NAME = "plan-and-execute"
TRACE_SERVICE_VERSION = "1.0.0"
METRICS_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_METRICS_INTERVAL = float(
    os.getenv("PLAN_EXECUTE_METRICS_INTERVAL_SECONDS", "15") or 15.0
)
DEFAULT_PROVIDER = "openai"


def _relative_path(path: Path) -> str:
    try:
        return os.path.relpath(path, start=LOG_DIR.parent)
    except ValueError:  # pragma: no cover - fallback for different drives
        return str(path)


class PlanExecute(TypedDict, total=False):
    """LangGraph state used for the benchmark."""

    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    response: str
    references: str
    replan_retry_attempts: int
    replan_previous_span_id: str


class Plan(BaseModel):
    """Structured representation of the planner output."""

    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )


class Response(BaseModel):
    """Final response returned to the user."""

    response: str


class Act(BaseModel):
    """Re-planner choice of responding or updating the plan."""

    action: Response | Plan = Field(
        description="If you want to respond to user, use Response. "
        "If you need to further use tools to get the answer, use Plan."
    )


@dataclass
class BenchmarkConfig:
    """CLI configuration for the benchmark."""

    question: str
    references: Optional[str]
    question_index: Optional[int]
    question_id: Optional[str]
    executor_model: str
    planner_model: str
    replanner_model: str
    prompt: str
    recursion_limit: int
    max_search_results: int
    agent_temperature: float
    verbose: bool
    metrics_interval: float
    evidence_source: str
    dataset_source: Optional[str]
    gold_answer: Optional[str]
    evaluator: str
    judge_model: Optional[str]
    provider: str
    judge_provider: str
    vertex_project: Optional[str]
    vertex_location: Optional[str]
    run_timeout_seconds: Optional[float]


@dataclass
class QuestionRecord:
    prompt: str
    references: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None


REFERENCE_COLUMNS = [
    ("gold_context", "Gold Context"),
    ("supporting_context", "Supporting Context"),
    ("supporting_facts", "Supporting Facts"),
    ("context", "Context"),
    ("references", "References"),
    ("distractors", "Distractor Passages"),
    ("evidence", "Evidence"),
]


def _extract_references_from_row(row: Dict[str, str]) -> Optional[str]:
    blocks: List[str] = []
    for column, label in REFERENCE_COLUMNS:
        value = row.get(column)
        if value:
            value = value.strip()
            if value:
                blocks.append(f"{label}:\n{value}")
    combined = "\n\n".join(blocks).strip()
    return combined or None


def load_questions(path: Path) -> List[QuestionRecord]:
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")
    suffix = path.suffix.lower()
    records: List[QuestionRecord] = []
    if suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return []
            lower_to_actual = {col.lower(): col for col in reader.fieldnames}
            question_col = lower_to_actual.get("question")
            if not question_col:
                raise ValueError("CSV must contain a 'question' column.")
            for row in reader:
                prompt = row.get(question_col, "").strip()
                if not prompt:
                    continue
                references = _extract_references_from_row(row)
                records.append(
                    QuestionRecord(prompt=prompt, references=references, metadata=row)
                )
        return records
    # Plaintext fallback: one question per line
    with path.open("r", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle if line.strip()]
    return [QuestionRecord(prompt=line) for line in lines]


def _question_with_references(question: str, references: Optional[str]) -> str:
    if references:
        return f"{question}\n\nReference passages:\n{references}"
    return question


def _make_llm(
    provider: str,
    model: str,
    temperature: float,
    *,
    vertex_project: Optional[str],
    vertex_location: Optional[str],
):
    if provider == "openai":
        return ChatOpenAI(model=model, temperature=temperature)
    if provider == "google-vertex":
        if ChatVertexAI is None:
            raise ImportError(
                "langchain-google-vertexai is required for provider=google-vertex. "
                "Install it via `pip install langchain-google-vertexai`."
            )
        project = vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise EnvironmentError(
                "Vertex AI provider requires --vertex-project or GOOGLE_CLOUD_PROJECT."
            )
        location = vertex_location or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        return ChatVertexAI(
            project=project,
            location=location,
            model_name=model,
            temperature=temperature,
        )
    raise ValueError(f"Unsupported provider: {provider}")


class ReferenceLookupTool(BaseTool):
    """Tool that returns dataset-provided passages instead of calling Tavily."""

    name: str = "reference_lookup"
    description: str = (
        "Return curated reference passages provided alongside the current question."
    )
    references: str = "No reference passages available."

    def _run(self, query: str, *args, **kwargs) -> str:  # type: ignore[override]
        return self.references

    async def _arun(self, query: str, *args, **kwargs) -> str:  # type: ignore[override]
        return self.references


def _append_callback(config: Optional[dict], callback) -> dict:
    """Attach a LangChain callback handler to the config without mutating input."""

    new_config: Dict[str, object] = dict(config or {})
    callbacks_entry = new_config.get("callbacks")

    if callbacks_entry is None:
        new_config["callbacks"] = [callback]
        return new_config

    if isinstance(callbacks_entry, (list, tuple)):
        callbacks_list = list(callbacks_entry)
        callbacks_list.append(callback)
        new_config["callbacks"] = callbacks_list
        return new_config

    add_handler = getattr(callbacks_entry, "add_handler", None)
    if callable(add_handler):
        if hasattr(callbacks_entry, "copy"):
            try:
                manager = callbacks_entry.copy()
            except Exception:  # pragma: no cover - fallback
                manager = callbacks_entry
        else:
            manager = callbacks_entry
        manager.add_handler(callback)
        new_config["callbacks"] = manager
        return new_config

    new_config["callbacks"] = [callbacks_entry, callback]
    return new_config


def ensure_env_vars(require_tavily: bool, provider: str, judge_provider: Optional[str], evaluator: str) -> None:
    """Make sure the APIs used by the benchmark are configured."""
    required = []
    if provider == "openai":
        required.append("OPENAI_API_KEY")
    elif provider == "google-vertex" and not (
        os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEXAI_PROJECT")
    ):
        raise RuntimeError(
            "Vertex provider requires GOOGLE_CLOUD_PROJECT or --vertex-project."
        )
    if require_tavily:
        required.append("TAVILY_API_KEY")
    if evaluator == "llm" and (judge_provider or provider) == "openai":
        required.append("OPENAI_API_KEY")
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Set them before running the benchmark."
        )


def _planner_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "For the given objective, come up with a simple step by step plan. "
                    "This plan should involve individual tasks, that if executed correctly"
                    " will yield the correct answer. Do not add any superfluous steps. "
                    "The result of the final step should be the final answer. Make sure "
                    "that each step has all the information needed - do not skip steps."
                ),
            ),
            ("placeholder", "{messages}"),
        ]
    )


def _replanner_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_template(
        (
            "For the given objective, come up with a simple step by step plan. "
            "This plan should involve individual tasks, that if executed correctly "
            "will yield the correct answer. Do not add any superfluous steps. "
            "The result of the final step should be the final answer. Make sure "
            "that each step has all the information needed - do not skip steps.\n\n"
            "Your objective was this:\n{input}\n\n"
            "Your original plan was this:\n{plan}\n\n"
            "You have currently done the follow steps:\n{past_steps}\n\n"
            "Update your plan accordingly. If no more steps are needed and you can "
            "return to the user, then respond with that. Otherwise, fill out the plan. "
            "Only add steps to the plan that still NEED to be done. "
            "Do not return previously done steps as part of the plan."
        )
    )


def build_plan_execute_app(
    bench_config: BenchmarkConfig, tracer: trace.Tracer | None = None
):
    """Create the LangGraph runnable for the benchmark."""

    use_tavily = bench_config.evidence_source == "tavily"
    gen_ai_system = (
        "google_vertex" if bench_config.provider == "google-vertex" else bench_config.provider
    )
    if use_tavily:
        tools = [TavilySearchResults(max_results=bench_config.max_search_results)]
    else:
        references = bench_config.references or ""
        tools = [ReferenceLookupTool(references=references)]
    exec_llm = _make_llm(
        bench_config.provider,
        bench_config.executor_model,
        bench_config.agent_temperature,
        vertex_project=bench_config.vertex_project,
        vertex_location=bench_config.vertex_location,
    )
    agent_executor = create_plan_agent(exec_llm, tools, prompt=bench_config.prompt)
    component_tracer = tracer or trace.get_tracer(APP_NAME)

    planner = _planner_prompt() | _make_llm(
        bench_config.provider,
        bench_config.planner_model,
        0,
        vertex_project=bench_config.vertex_project,
        vertex_location=bench_config.vertex_location,
    ).with_structured_output(Plan)
    replanner = _replanner_prompt() | _make_llm(
        bench_config.provider,
        bench_config.replanner_model,
        0,
        vertex_project=bench_config.vertex_project,
        vertex_location=bench_config.vertex_location,
    ).with_structured_output(Act)

    async def execute_step(state: PlanExecute, config: RunnableConfig | None = None):
        plan = state["plan"]
        if not plan:
            with invoke_agent_span(
                component_tracer,
                "plan_execute.node.agent",
                agent_name=f"{APP_NAME}.node.agent",
                payload={"plan": plan, "past_steps": state.get("past_steps", [])},
                extra_attributes={"plan_execute.plan.step_count": 0},
            ) as (node_span, _):
                if node_span:
                    node_span.set_status(Status(StatusCode.ERROR, "empty_plan"))
                    set_agent_failure_attributes(
                        node_span,
                        category=AgentFailureCategory.QUALITY,
                        reason="empty_plan",
                    )
                    set_agent_usefulness(
                        node_span,
                        is_useless=True,
                        reason="empty_plan",
                    )
                return {
                    "response": "",
                    "past_steps": state.get("past_steps", []),
                    "plan": plan,
                }
        plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
        task = plan[0]
        references = state.get("references")
        task_formatted = (
            f"For the following plan:\n{plan_str}\n\n"
            f"You are tasked with executing step 1, {task}."
        )
        if references:
            task_formatted += (
                "\n\nReference passages (use these instead of searching unnecessarily):\n"
                f"{references}"
            )
        payload = {
            "task": task,
            "plan_length": len(plan),
            "past_steps": state.get("past_steps", []),
        }
        with invoke_agent_span(
            component_tracer,
            "plan_execute.node.agent",
            agent_name=f"{APP_NAME}.node.agent",
            payload=payload,
            extra_attributes={"plan_execute.node": "agent"},
        ) as (node_span, input_bytes):
            usage_callback = LangChainUsageCallback()
            invoke_config = _append_callback(config, usage_callback)
            agent_response = await agent_executor.ainvoke(
                {"messages": [("user", task_formatted)]},
                config=invoke_config,
            )
            if node_span:
                record_usage_on_span(node_span, usage_callback)
            content = agent_response["messages"][-1].content
            output_payload = {"task": task, "result": content}
            if node_span:
                record_invoke_agent_output(node_span, output_payload, input_bytes)
            return {"past_steps": [(task, content)]}

    async def plan_step(state: PlanExecute, config: RunnableConfig | None = None):
        with invoke_agent_span(
            component_tracer,
            "plan_execute.node.planner",
            agent_name=f"{APP_NAME}.node.planner",
            payload={"input": state["input"]},
            extra_attributes={"plan_execute.node": "planner"},
        ) as (node_span, _):

            def _invoke(updated_config):
                question = _question_with_references(
                    state["input"], state.get("references")
                )
                return planner.invoke(
                    {"messages": [("user", question)]}, config=updated_config
                )

            def _annotate_plan(span, plan_result):
                steps = getattr(plan_result, "steps", None) or []
                if not steps:
                    set_agent_failure_attributes(
                        span,
                        category=AgentFailureCategory.QUALITY,
                        reason="Planner returned no steps.",
                    )
                    set_agent_usefulness(
                        span,
                        is_useless=True,
                        reason="LLM plan was empty.",
                    )
                else:
                    set_agent_usefulness(
                        span,
                        is_useless=False,
                        reason="Planner produced actionable steps.",
                    )

            plan = run_llm_with_span(
                component_tracer,
                "plan_execute.call_llm.planner",
                agent_name=f"{APP_NAME}.llm.planner",
                phase="planner",
                config=config,
                invoke_fn=_invoke,
                extra_attributes={
                    "gen_ai.system": gen_ai_system,
                    "gen_ai.request.model": bench_config.planner_model,
                },
                postprocess_fn=_annotate_plan,
            )
            steps = plan.steps
            if node_span:
                node_span.set_attribute("plan_execute.plan.step_count", len(steps))
                preview = steps[:3] if len(steps) > 3 else steps
                node_span.set_attribute("plan_execute.plan.preview", preview)
            return {"plan": steps}

    async def replan_step(state: PlanExecute, config: RunnableConfig | None = None):
        payload = {
            "input": state["input"],
            "plan": state.get("plan"),
            "past_steps": state.get("past_steps"),
        }
        with invoke_agent_span(
            component_tracer,
            "plan_execute.node.replan",
            agent_name=f"{APP_NAME}.node.replan",
            payload=payload,
            extra_attributes={"plan_execute.node": "replan"},
        ) as (node_span, _):
            prior_attempts = int(state.get("replan_retry_attempts") or 0)
            previous_span_id = state.get("replan_previous_span_id")
            retry_context = {
                "retry": {
                    "attempt_number": prior_attempts + 1,
                    "trigger": AgentRetryTrigger.QUALITY,
                    "reason": "Replanning after agent deferred responding.",
                }
            }
            if previous_span_id:
                retry_context["retry"]["previous_span_id"] = previous_span_id

            def _invoke(updated_config):
                state_payload = dict(state)
                state_payload["input"] = _question_with_references(
                    state["input"], state.get("references")
                )
                return replanner.invoke(state_payload, config=updated_config)

            def _annotate_replan(span, result):
                span_hex = span_id_hex(span)
                if span_hex:
                    state["replan_previous_span_id"] = span_hex
                failures = int(state.get("replan_retry_attempts") or 0)
                if isinstance(result.action, Response):
                    set_agent_usefulness(
                        span,
                        is_useless=False,
                        reason="Replanner produced final response.",
                    )
                    state["replan_retry_attempts"] = 0
                    return
                steps = getattr(result.action, "steps", None) or []
                state["replan_retry_attempts"] = failures + 1
                if not steps:
                    set_agent_failure_attributes(
                        span,
                        category=AgentFailureCategory.QUALITY,
                        reason="Replanner returned zero steps.",
                    )
                    set_agent_usefulness(
                        span,
                        is_useless=True,
                        reason="No steps to execute.",
                    )
                else:
                    set_agent_usefulness(
                        span,
                        is_useless=False,
                        reason="Replanner updated the plan.",
                    )

            output = run_llm_with_span(
                component_tracer,
                "plan_execute.call_llm.replanner",
                agent_name=f"{APP_NAME}.llm.replanner",
                phase="replanner",
                config=config,
                invoke_fn=_invoke,
                extra_attributes={
                    "gen_ai.system": gen_ai_system,
                    "gen_ai.request.model": bench_config.replanner_model,
                },
                agent_context=retry_context,
                postprocess_fn=_annotate_replan,
            )
            if output is None:
                if node_span:
                    node_span.set_status(Status(StatusCode.ERROR, "replanner_returned_none"))
                    set_agent_failure_attributes(
                        node_span,
                        category=AgentFailureCategory.SYSTEM,
                        reason="replanner_returned_none",
                    )
                    set_agent_usefulness(
                        node_span, is_useless=True, reason="replanner_returned_none"
                    )
                # End the graph with an empty response to avoid further errors.
                return {
                    "response": "",
                    "replan_retry_attempts": int(state.get("replan_retry_attempts") or 0),
                    "replan_previous_span_id": state.get("replan_previous_span_id"),
                }
            if isinstance(output.action, Response):
                if node_span:
                    node_span.set_attribute("plan_execute.replan.action", "respond")
                return {
                    "response": output.action.response,
                    "replan_retry_attempts": int(state.get("replan_retry_attempts") or 0),
                    "replan_previous_span_id": state.get("replan_previous_span_id"),
                }
            if node_span:
                node_span.set_attribute("plan_execute.replan.action", "plan")
                node_span.set_attribute(
                    "plan_execute.replan.steps", len(output.action.steps)
                )
            return {
                "plan": output.action.steps,
                "replan_retry_attempts": int(state.get("replan_retry_attempts") or 0),
                "replan_previous_span_id": state.get("replan_previous_span_id"),
            }

    def should_end(state: PlanExecute):
        if state.get("response"):
            return END
        return "agent"

    workflow = StateGraph(PlanExecute)
    workflow.add_node("planner", plan_step)
    workflow.add_node("agent", execute_step)
    workflow.add_node("replan", replan_step)
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "agent")
    workflow.add_edge("agent", "replan")
    workflow.add_conditional_edges("replan", should_end, ["agent", END])
    return workflow.compile()


def _jsonable(value):
    """Convert LangChain objects into JSON-safe data."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, BaseMessage):
        return {
            "type": value.type,
            "content": value.content,
            "additional_kwargs": value.additional_kwargs,
        }
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _summarize_event(node: str, payload) -> str:
    """Generate a concise console message for the event."""
    if node == "planner" and isinstance(payload, dict) and "plan" in payload:
        plan = payload["plan"]
        return f"Initial plan ({len(plan)} steps): " + " | ".join(plan)
    if node == "agent" and isinstance(payload, dict) and "past_steps" in payload:
        step, result = payload["past_steps"][0]
        return f"Agent executed: {step} -> {result[:200]}"
    if node == "replan":
        if isinstance(payload, dict) and payload.get("response"):
            return f"Response ready: {payload['response']}"
        if isinstance(payload, dict) and payload.get("plan"):
            return "Revised plan: " + " | ".join(payload["plan"])
    if node == "__end__":
        if isinstance(payload, dict) and payload.get("response"):
            return f"Final answer: {payload['response']}"
    return f"{node}: {payload}"


def _stream_events(
    app,
    config: BenchmarkConfig,
    log_handle,
    tracer: trace.Tracer | None = None,
    judge_llm=None,
):
    """Return coroutine that runs the benchmark and logs every event."""

    async def _runner():
        final_response = None
        judgement = None
        judgement_reason = None
        start_time = time.perf_counter()
        run_attrs = {
            "question": config.question,
            "executor_model": config.executor_model,
            "planner_model": config.planner_model,
            "replanner_model": config.replanner_model,
            "max_search_results": config.max_search_results,
            "agent_temperature": config.agent_temperature,
            "question_index": config.question_index,
            "evidence_source": config.evidence_source,
            "references_available": bool(config.references),
        }
        run_context = (
            invoke_agent_span(
                tracer,
                "plan_execute.run",
                agent_name=f"{APP_NAME}.run",
                payload={"question": config.question},
                extra_attributes=run_attrs,
            )
            if tracer
            else nullcontext((None, 0))
        )
        with run_context as (run_span, input_bytes):
            initial_state: Dict[str, object] = {
                "input": config.question,
                "replan_retry_attempts": 0,
                "replan_previous_span_id": None,
            }
            if config.references:
                initial_state["references"] = config.references
            try:
                async for event in app.astream(
                    initial_state, config={"recursion_limit": config.recursion_limit}
                ):
                    for node, payload in event.items():
                        check_timeout(start_time, config.run_timeout_seconds)
                        record = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "node": node,
                            "payload": _jsonable(payload),
                        }
                        log_handle.write(json.dumps(record) + "\n")
                        log_handle.flush()
                        if config.verbose:
                            LOGGER.info(_summarize_event(node, payload))
                        if node == "__end__":
                            final_response = payload.get("response")
                        elif node == "replan" and isinstance(payload, dict) and payload.get("response"):
                            # Some runs may not emit a separate __end__ event; capture final response here as well.
                            final_response = payload.get("response")
            except Exception as exc:
                if run_span:
                    run_span.record_exception(exc)
                    run_span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            finally:
                judgement, judgement_reason = evaluate_answer(
                    mode=config.evaluator,
                    pred=final_response,
                    gold=config.gold_answer,
                    question=config.question,
                    llm=judge_llm,
                )
                record_run_judgement(run_span, judgement, judgement_reason)
                if run_span:
                    set_run_outcome(
                        run_span,
                        success=bool(final_response),
                        reason="timeout" if not final_response and judgement == "unknown" else "completed",
                    )
                if run_span and final_response is not None:
                    record_invoke_agent_output(run_span, final_response, input_bytes)
        return final_response, judgement, judgement_reason

    return _runner()


def _write_metadata(path: Path, run_id: str, config: BenchmarkConfig, status: str, **extra):
    trace_log = extra.pop("trace_log", None)
    metrics_log = extra.pop("metrics_log", None)
    metadata = {
        "metadata_version": RUN_METADATA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "app_name": "plan_and_execute_benchmark",
        "question": config.question,
        "question_index": config.question_index,
        "question_id": config.question_id,
        "executor_model": config.executor_model,
        "planner_model": config.planner_model,
        "replanner_model": config.replanner_model,
        "recursion_limit": config.recursion_limit,
        "max_search_results": config.max_search_results,
        "agent_temperature": config.agent_temperature,
        "evidence_source": config.evidence_source,
        "dataset_source": config.dataset_source,
        "references_present": bool(config.references),
        "gold_answer_present": bool(config.gold_answer),
        "evaluator": config.evaluator,
        "judge_model": config.judge_model,
        "judge_provider": config.judge_provider,
        "provider": config.provider,
        "vertex_project": config.vertex_project,
        "vertex_location": config.vertex_location,
        "status": status,
        "cli_argv": sys.argv[1:],
        "env_vars_present": {
            "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
            "TAVILY_API_KEY": bool(os.getenv("TAVILY_API_KEY")),
        },
    }
    if trace_log:
        metadata["trace_log"] = trace_log
    if metrics_log:
        metadata["metrics_log"] = metrics_log
    metadata.update({k: v for k, v in extra.items() if v is not None})
    path.write_text(json.dumps(metadata, indent=2))


async def run_benchmark(
    config: BenchmarkConfig,
    run_id: str,
    tracer: trace.Tracer | None = None,
    trace_log_path: Path | None = None,
    metrics_log_path: Path | None = None,
    judge_llm=None,
) -> str | None:
    """Entry point used by asyncio.run."""
    ensure_env_vars(
        config.evidence_source == "tavily",
        provider=config.provider,
        judge_provider=config.judge_provider,
        evaluator=config.evaluator,
    )
    app = build_plan_execute_app(config, tracer=tracer)
    log_path = LOG_DIR / f"run_{run_id}.jsonl"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"
    status = "unknown"
    error_message = None
    final_response = None
    judgement = None
    judgement_reason = None

    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            final_response, judgement, judgement_reason = await _stream_events(
                app, config, log_handle, tracer=tracer, judge_llm=judge_llm
            )
        status = "success"
        return final_response
    except GraphRecursionError as exc:
        error_message = str(exc)
        status = "failed"
        LOGGER.error("Graph recursion limit reached: %s", exc)
        return None
    except TimeoutError as exc:
        error_message = str(exc)
        status = "failed"
        LOGGER.error("Run timed out: %s", exc)
        return None
    except Exception as exc:
        error_message = repr(exc)
        status = "failed"
        raise
    finally:
        _write_metadata(
            metadata_path,
            run_id,
            config,
            status=status,
            final_response=final_response,
            error=error_message,
            judgement=judgement,
            judgement_reason=judgement_reason,
            event_log=_relative_path(log_path),
            trace_log=_relative_path(trace_log_path) if trace_log_path else None,
            metrics_log=_relative_path(metrics_log_path) if metrics_log_path else None,
        )


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan-and-Execute LangGraph benchmark translated from the notebook."
    )
    parser.add_argument(
        "--question",
        default=DEFAULT_OBJECTIVE,
        help="Objective or question to hand to the plan-and-execute agent.",
    )
    parser.add_argument(
        "--provider",
        choices=("openai", "google-vertex"),
        default=DEFAULT_PROVIDER,
        help="LLM provider (default: openai).",
    )
    parser.add_argument(
        "--vertex-project",
        help="Google Cloud project for Vertex AI (defaults to GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--vertex-location",
        help="Vertex AI region (defaults to GOOGLE_CLOUD_REGION or us-central1).",
    )
    parser.add_argument(
        "--questions-file",
        help="Optional CSV/plaintext file containing questions (with optional references).",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="0-based index into the dataset to start from when using --questions-file.",
    )
    parser.add_argument(
        "--num-questions",
        type=int,
        default=1,
        help="Number of questions to run sequentially from the dataset.",
    )
    parser.add_argument(
        "--evidence-source",
        choices=("tavily", "dataset"),
        default="tavily",
        help="Use Tavily search (default) or dataset-provided references as the tool context.",
    )
    parser.add_argument(
        "--executor-model",
        default="gpt-4o-mini",
        help="Model used by the ReAct executor that performs each plan step.",
    )
    parser.add_argument(
        "--planner-model",
        default="gpt-5-mini",
        help="Model that proposes the initial plan.",
    )
    parser.add_argument(
        "--replanner-model",
        default="gpt-5-mini",
        help="Model that evaluates progress and decides to replan or respond.",
    )
    parser.add_argument(
        "--agent-temperature",
        type=float,
        default=0,
        help="Sampling temperature for the executor model.",
    )
    parser.add_argument(
        "--max-search-results",
        type=int,
        default=3,
        help="How many Tavily results to request per tool invocation.",
    )
    parser.add_argument(
        "--prompt",
        default="You are a helpful assistant.",
        help="System prompt passed to the ReAct executor.",
    )
    parser.add_argument(
        "--recursion-limit",
        type=int,
        default=50,
        help="LangGraph recursion limit – caps plan/replan loops.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Silence per-node summaries (logs are still written under logs/).",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=DEFAULT_METRICS_INTERVAL,
        help=(
            "Seconds between psutil samples for system metrics "
            f"(default {DEFAULT_METRICS_INTERVAL}, override via PLAN_EXECUTE_METRICS_INTERVAL_SECONDS)."
        ),
    )
    parser.add_argument(
        "--evaluator",
        choices=("f1", "llm"),
        default="f1",
        help="Correctness evaluator to use for run.judgement (default: f1).",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Model for LLM-as-a-judge when --evaluator llm is selected (defaults to --executor-model).",
    )
    parser.add_argument(
        "--judge-provider",
        choices=("openai", "google-vertex"),
        default=None,
        help="Provider for the LLM judge (default: same as --provider).",
    )
    parser.add_argument(
        "--run-timeout-seconds",
        type=float,
        default=None,
        help="Optional wall-clock timeout per question; exits early with failure if exceeded.",
    )
    args = parser.parse_args(argv)
    if args.num_questions <= 0:
        parser.error("--num-questions must be positive.")
    if args.start_index < 0:
        parser.error("--start-index must be non-negative.")
    if args.provider == "google-vertex" and not (
        args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT")
    ):
        parser.error(
            "provider=google-vertex requires --vertex-project or GOOGLE_CLOUD_PROJECT."
        )
    return args


def main():
    args = parse_args()
    question_records: List[Tuple[int, QuestionRecord]] = []
    dataset_source: Optional[str] = None
    if args.questions_file:
        dataset_path = Path(args.questions_file)
        dataset_source = str(dataset_path)
        all_questions = load_questions(dataset_path)
        if not all_questions:
            raise SystemExit(f"No questions found in {dataset_path}")
        if args.start_index >= len(all_questions):
            raise SystemExit("Start index exceeds dataset length.")
        end = min(len(all_questions), args.start_index + args.num_questions)
        subset = all_questions[args.start_index:end]
        if args.evidence_source == "dataset":
            missing = [
                args.start_index + offset
                for offset, record in enumerate(subset)
                if not record.references
            ]
            if missing:
                preview = ", ".join(str(idx) for idx in missing[:5])
                raise SystemExit(
                    "Dataset evidence selected but missing reference passages for "
                    f"indices: {preview}"
                )
        question_records = [
            (idx, record) for idx, record in enumerate(subset, start=args.start_index)
        ]
    else:
        if args.evidence_source == "dataset":
            raise SystemExit(
                "--evidence-source dataset requires --questions-file with reference passages."
            )
        question_records = [(0, QuestionRecord(prompt=args.question))]

    base_kwargs = {
        "executor_model": args.executor_model,
        "planner_model": args.planner_model,
        "replanner_model": args.replanner_model,
        "prompt": args.prompt,
        "recursion_limit": args.recursion_limit,
        "max_search_results": args.max_search_results,
        "agent_temperature": args.agent_temperature,
        "verbose": not args.quiet,
        "metrics_interval": max(1.0, args.metrics_interval),
        "evidence_source": args.evidence_source,
        "dataset_source": dataset_source,
        "evaluator": args.evaluator,
        "judge_model": args.judge_model or args.executor_model,
        "judge_provider": args.judge_provider or args.provider,
        "provider": args.provider,
        "vertex_project": args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
        "vertex_location": args.vertex_location,
        "run_timeout_seconds": args.run_timeout_seconds,
    }
    judge_llm = None
    if args.evaluator == "llm":
        model_for_judge = args.judge_model or args.executor_model
        judge_provider = args.judge_provider or args.provider
        try:
            judge_llm = _make_llm(
                judge_provider,
                model_for_judge,
                0,
                vertex_project=args.vertex_project or os.getenv("GOOGLE_CLOUD_PROJECT"),
                vertex_location=args.vertex_location,
            )
        except Exception as exc:  # pragma: no cover - optional judge
            LOGGER.warning("LLM judge unavailable, falling back to unknown judgements: %s", exc)
            judge_llm = None

    for dataset_index, record in question_records:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        tracer: Optional[trace.Tracer] = None
        trace_log_path: Optional[Path] = None
        provider: Optional[TracerProvider] = None
        metrics_recorder: Optional[PsutilMetricsRecorder] = None
        metrics_log_path: Optional[Path] = None
        references = record.references if args.evidence_source == "dataset" else None
        config = BenchmarkConfig(
            question=record.prompt,
            references=references,
            question_index=dataset_index if dataset_source else None,
            question_id=(record.metadata or {}).get("id") if record.metadata else None,
            gold_answer=(record.metadata or {}).get("answer") if record.metadata else None,
            **base_kwargs,
        )
        LOGGER.info("Question %s: %s", dataset_index, record.prompt)
        try:
            tracer, trace_log_path, provider = setup_jsonl_tracing(
                app_name=APP_NAME,
                service_name=TRACE_SERVICE_NAME,
                service_version=TRACE_SERVICE_VERSION,
                log_dir=LOG_DIR,
                run_id=run_id,
                environment=DEFAULT_ENVIRONMENT,
                set_global_provider=False,
            )
            LOGGER.info("OpenTelemetry trace log: %s", trace_log_path)
        except Exception as exc:  # pragma: no cover - tracing is optional
            LOGGER.warning("Unable to initialize OpenTelemetry tracing: %s", exc)
            tracer = None
            trace_log_path = None
            provider = None
        try:
            metrics_recorder = PsutilMetricsRecorder(
                service_name=TRACE_SERVICE_NAME,
                service_version=TRACE_SERVICE_VERSION,
                run_id=run_id,
                output_dir=METRICS_DIR,
                environment=DEFAULT_ENVIRONMENT,
                scope=f"{APP_NAME}.system-metrics",
                interval_seconds=config.metrics_interval,
                logger=LOGGER,
            )
            metrics_log_path = metrics_recorder.output_path
            metrics_recorder.start()
            LOGGER.info("System metrics log: %s", metrics_log_path)
        except Exception as exc:  # pragma: no cover - metrics optional
            LOGGER.warning("Unable to initialize system metrics recorder: %s", exc)
            metrics_recorder = None
            metrics_log_path = None
        try:
            final_answer = asyncio.run(
                run_benchmark(
                    config,
                    run_id,
                    tracer=tracer,
                    trace_log_path=trace_log_path,
                    metrics_log_path=metrics_log_path,
                    judge_llm=judge_llm,
                )
            )
        except KeyboardInterrupt:
            LOGGER.warning("Benchmark interrupted by user.")
            break
        except Exception:
            raise
        else:
            if final_answer:
                LOGGER.info("Final response: %s", final_answer)
            else:
                LOGGER.info("Benchmark finished but no response was produced.")
        finally:
            if provider:
                try:
                    provider.force_flush()
                except Exception:  # pragma: no cover - best effort
                    pass
            if metrics_recorder:
                metrics_recorder.stop()
            if provider:
                provider.shutdown()


if __name__ == "__main__":
    main()
