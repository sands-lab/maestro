"""Command-line translation of the Plan-and-Execute notebook."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import operator
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Tuple

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

warnings.filterwarnings(
    "ignore",
    message=r"The class `TavilySearchResults` was deprecated.*",
    category=DeprecationWarning,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("plan-and-execute")
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_OBJECTIVE = "what is the hometown of the mens 2024 Australia open winner?"
RUN_METADATA_VERSION = 1


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


def build_plan_execute_app(config: BenchmarkConfig):
    """Create the LangGraph runnable for the benchmark."""
    tools = [TavilySearchResults(max_results=config.max_search_results)]
    exec_llm = ChatOpenAI(model=config.executor_model, temperature=config.agent_temperature)
    agent_executor = create_react_agent(exec_llm, tools, prompt=config.prompt)

    planner = _planner_prompt() | ChatOpenAI(
        model=config.planner_model, temperature=0
    ).with_structured_output(Plan)
    replanner = _replanner_prompt() | ChatOpenAI(
        model=config.replanner_model, temperature=0
    ).with_structured_output(Act)

    async def execute_step(state: PlanExecute):
        plan = state["plan"]
        plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
        task = plan[0]
        task_formatted = (
            f"For the following plan:\n{plan_str}\n\n"
            f"You are tasked with executing step 1, {task}."
        )
        agent_response = await agent_executor.ainvoke(
            {"messages": [("user", task_formatted)]}
        )
        content = agent_response["messages"][-1].content
        return {"past_steps": [(task, content)]}

    async def plan_step(state: PlanExecute):
        plan = await planner.ainvoke({"messages": [("user", state["input"])]})
        return {"plan": plan.steps}

    async def replan_step(state: PlanExecute):
        output = await replanner.ainvoke(state)
        if isinstance(output.action, Response):
            return {"response": output.action.response}
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


def _stream_events(app, config: BenchmarkConfig, log_handle):
    """Return coroutine that runs the benchmark and logs every event."""

    async def _runner():
        final_response = None
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
        return final_response

    return _runner()


def _write_metadata(path: Path, run_id: str, config: BenchmarkConfig, status: str, **extra):
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
    metadata.update({k: v for k, v in extra.items() if v is not None})
    path.write_text(json.dumps(metadata, indent=2))


async def run_benchmark(config: BenchmarkConfig) -> str | None:
    """Entry point used by asyncio.run."""
    ensure_env_vars()
    app = build_plan_execute_app(config)
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{run_id}.jsonl"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"
    status = "unknown"
    error_message = None
    final_response = None

    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            final_response = await _stream_events(app, config, log_handle)
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
        default="gpt-4o",
        help="Model that proposes the initial plan.",
    )
    parser.add_argument(
        "--replanner-model",
        default="gpt-4o",
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
    )


def main():
    config = parse_args()
    try:
        final_answer = asyncio.run(run_benchmark(config))
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


if __name__ == "__main__":
    main()
