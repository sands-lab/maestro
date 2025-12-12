"""Command-line translation of the Plan-and-Execute notebook."""

import argparse
import asyncio
import json
import logging
import operator
import os
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
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mas_traces.langgraph_otel import (
    DEFAULT_ENVIRONMENT,
    LangChainUsageCallback,
    PsutilMetricsRecorder,
    invoke_agent_span,
    record_invoke_agent_output,
    record_usage_on_span,
    run_llm_with_span,
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
GEN_AI_SYSTEM = "openai"


def _relative_path(path: Path) -> str:
    try:
        return os.path.relpath(path, start=LOG_DIR.parent)
    except ValueError:  # pragma: no cover - fallback for different drives
        return str(path)


class PlanExecute(TypedDict):
    """LangGraph state used for the benchmark."""

    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    response: str


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
    executor_model: str
    planner_model: str
    replanner_model: str
    prompt: str
    recursion_limit: int
    max_search_results: int
    agent_temperature: float
    verbose: bool
    metrics_interval: float


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


def ensure_env_vars() -> None:
    """Make sure the APIs used by the benchmark are configured."""
    missing = [var for var in ("OPENAI_API_KEY", "TAVILY_API_KEY") if not os.getenv(var)]
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

    tools = [TavilySearchResults(max_results=bench_config.max_search_results)]
    exec_llm = ChatOpenAI(
        model=bench_config.executor_model, temperature=bench_config.agent_temperature
    )
    agent_executor = create_plan_agent(exec_llm, tools, prompt=bench_config.prompt)
    component_tracer = tracer or trace.get_tracer(APP_NAME)

    planner = _planner_prompt() | ChatOpenAI(
        model=bench_config.planner_model, temperature=0
    ).with_structured_output(Plan)
    replanner = _replanner_prompt() | ChatOpenAI(
        model=bench_config.replanner_model, temperature=0
    ).with_structured_output(Act)

    async def execute_step(state: PlanExecute, config: RunnableConfig | None = None):
        plan = state["plan"]
        plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
        task = plan[0]
        task_formatted = (
            f"For the following plan:\n{plan_str}\n\n"
            f"You are tasked with executing step 1, {task}."
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
                return planner.invoke(
                    {"messages": [("user", state["input"])]}, config=updated_config
                )

            plan = run_llm_with_span(
                component_tracer,
                "plan_execute.call_llm.planner",
                agent_name=f"{APP_NAME}.llm.planner",
                phase="planner",
                config=config,
                invoke_fn=_invoke,
                extra_attributes={
                    "gen_ai.system": GEN_AI_SYSTEM,
                    "gen_ai.request.model": bench_config.planner_model,
                },
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

            def _invoke(updated_config):
                return replanner.invoke(state, config=updated_config)

            output = run_llm_with_span(
                component_tracer,
                "plan_execute.call_llm.replanner",
                agent_name=f"{APP_NAME}.llm.replanner",
                phase="replanner",
                config=config,
                invoke_fn=_invoke,
                extra_attributes={
                    "gen_ai.system": GEN_AI_SYSTEM,
                    "gen_ai.request.model": bench_config.replanner_model,
                },
            )
            if isinstance(output.action, Response):
                if node_span:
                    node_span.set_attribute("plan_execute.replan.action", "respond")
                return {"response": output.action.response}
            if node_span:
                node_span.set_attribute("plan_execute.replan.action", "plan")
                node_span.set_attribute(
                    "plan_execute.replan.steps", len(output.action.steps)
                )
            return {"plan": output.action.steps}

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


def _stream_events(app, config: BenchmarkConfig, log_handle, tracer: trace.Tracer | None = None):
    """Return coroutine that runs the benchmark and logs every event."""

    async def _runner():
        final_response = None
        run_attrs = {
            "question": config.question,
            "executor_model": config.executor_model,
            "planner_model": config.planner_model,
            "replanner_model": config.replanner_model,
            "max_search_results": config.max_search_results,
            "agent_temperature": config.agent_temperature,
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
            async for event in app.astream(
                {"input": config.question}, config={"recursion_limit": config.recursion_limit}
            ):
                for node, payload in event.items():
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
            if run_span and final_response is not None:
                record_invoke_agent_output(run_span, final_response, input_bytes)
        return final_response

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
        "executor_model": config.executor_model,
        "planner_model": config.planner_model,
        "replanner_model": config.replanner_model,
        "recursion_limit": config.recursion_limit,
        "max_search_results": config.max_search_results,
        "agent_temperature": config.agent_temperature,
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
) -> str | None:
    """Entry point used by asyncio.run."""
    ensure_env_vars()
    app = build_plan_execute_app(config, tracer=tracer)
    log_path = LOG_DIR / f"run_{run_id}.jsonl"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"
    status = "unknown"
    error_message = None
    final_response = None

    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            final_response = await _stream_events(app, config, log_handle, tracer=tracer)
        status = "success"
        return final_response
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
            event_log=_relative_path(log_path),
            trace_log=_relative_path(trace_log_path) if trace_log_path else None,
            metrics_log=_relative_path(metrics_log_path) if metrics_log_path else None,
        )


def parse_args(argv: List[str] | None = None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(
        description="Plan-and-Execute LangGraph benchmark translated from the notebook."
    )
    parser.add_argument(
        "--question",
        default=DEFAULT_OBJECTIVE,
        help="Objective or question to hand to the plan-and-execute agent.",
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
    args = parser.parse_args(argv)
    return BenchmarkConfig(
        question=args.question,
        executor_model=args.executor_model,
        planner_model=args.planner_model,
        replanner_model=args.replanner_model,
        prompt=args.prompt,
        recursion_limit=args.recursion_limit,
        max_search_results=args.max_search_results,
        agent_temperature=args.agent_temperature,
        verbose=not args.quiet,
        metrics_interval=max(1.0, args.metrics_interval),
    )


def main():
    config = parse_args()
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tracer = None
    trace_log_path = None
    provider = None
    metrics_recorder: Optional[PsutilMetricsRecorder] = None
    metrics_log_path: Optional[Path] = None
    try:
        tracer, trace_log_path, provider = setup_jsonl_tracing(
            app_name=APP_NAME,
            service_name=TRACE_SERVICE_NAME,
            service_version=TRACE_SERVICE_VERSION,
            log_dir=LOG_DIR,
            run_id=run_id,
            environment=DEFAULT_ENVIRONMENT,
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
            interval_seconds=max(1.0, config.metrics_interval),
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
            )
        )
    except KeyboardInterrupt:
        LOGGER.warning("Benchmark interrupted by user.")
        return
    except Exception:
        raise
    else:
        if final_answer:
            LOGGER.info("Final response: %s", final_answer)
        else:
            LOGGER.info("Benchmark finished but no response was produced.")
    finally:
        if metrics_recorder:
            metrics_recorder.stop()
        if provider:
            provider.shutdown()


if __name__ == "__main__":
    main()
