"""CLI benchmark derived from the Language Agent Tree Search notebook."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict, deque
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers.openai_tools import (
    JsonOutputToolsParser,
    PydanticToolsParser,
)
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.prompt_values import ChatPromptValue
from langchain_core.runnables import RunnableConfig, chain as as_runnable
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
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
    run_tool_with_span,
    setup_jsonl_tracing,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("lats-benchmark")

APP_NAME = "language_agent_tree_search"
BENCHMARK_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = BENCHMARK_ROOT / "data" / "questions.csv"
LOG_DIR = BENCHMARK_ROOT / "logs"
METADATA_VERSION = 1
TRACE_SERVICE_NAME = "language-agent-tree-search"
TRACE_SERVICE_VERSION = "1.0.0"
METRICS_DIR = BENCHMARK_ROOT / "metrics"
DEFAULT_METRICS_INTERVAL = float(os.getenv("LATS_METRICS_INTERVAL_SECONDS", "15") or 15.0)
GEN_AI_SYSTEM = "openai"


class Reflection(BaseModel):
    reflections: str = Field(
        description="Thoughtful critique of the candidate response quality."
    )
    score: int = Field(
        description="Score from 0-10 for the candidate response.",
        ge=0,
        le=10,
    )
    found_solution: bool = Field(
        description="True if the candidate fully solves the task."
    )

    def as_message(self) -> HumanMessage:
        return HumanMessage(
            content=f"Reasoning: {self.reflections}\nScore: {self.score}"
        )

    @property
    def normalized_score(self) -> float:
        return self.score / 10.0


class Node:
    """Tree node that tracks LangGraph message trajectories and reflection scores."""

    def __init__(
        self,
        messages: List[BaseMessage],
        reflection: Reflection,
        parent: Optional["Node"] = None,
    ):
        self.messages = messages
        self.parent = parent
        self.children: List[Node] = []
        self.value = 0.0
        self.visits = 0
        self.reflection = reflection
        self.depth = parent.depth + 1 if parent is not None else 1
        self._is_solved = reflection.found_solution if reflection else False
        if self._is_solved:
            self._mark_tree_as_solved()
        self.backpropagate(reflection.normalized_score)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Node depth={self.depth} value={self.value:.3f} visits={self.visits} "
            f"children={len(self.children)} solved={self._is_solved}>"
        )

    @property
    def is_solved(self) -> bool:
        return self._is_solved

    @property
    def is_terminal(self) -> bool:
        return not self.children

    @property
    def best_child_score(self) -> Optional["Node"]:
        if not self.children:
            return None
        return max(self.children, key=lambda child: int(child.is_solved) * child.value)

    @property
    def height(self) -> int:
        if self.children:
            return 1 + max(child.height for child in self.children)
        return 1

    def upper_confidence_bound(self, exploration_weight: float = 1.0) -> float:
        if self.parent is None:
            raise ValueError("Cannot compute UCT for root node")
        if self.visits == 0:
            return self.value
        average_reward = self.value / self.visits
        exploration_term = math.sqrt(math.log(self.parent.visits) / self.visits)
        return average_reward + exploration_weight * exploration_term

    def backpropagate(self, reward: float) -> None:
        node: Optional[Node] = self
        while node:
            node.visits += 1
            node.value = (node.value * (node.visits - 1) + reward) / node.visits
            node = node.parent

    def get_messages(self, include_reflections: bool = True) -> List[BaseMessage]:
        if include_reflections:
            return self.messages + [self.reflection.as_message()]
        return self.messages

    def get_trajectory(self, include_reflections: bool = True) -> List[BaseMessage]:
        messages: List[BaseMessage] = []
        node: Optional[Node] = self
        while node:
            messages.extend(
                node.get_messages(include_reflections=include_reflections)[::-1]
            )
            node = node.parent
        return messages[::-1]

    def _get_all_children(self) -> List["Node"]:
        all_nodes: List[Node] = []
        nodes: deque[Node] = deque()
        nodes.append(self)
        while nodes:
            node = nodes.popleft()
            all_nodes.extend(node.children)
            for child in node.children:
                nodes.append(child)
        return all_nodes

    def get_best_solution(self) -> "Node":
        all_nodes = [self] + self._get_all_children()
        return max(
            all_nodes,
            key=lambda node: int(node.is_terminal and node.is_solved) * node.value,
        )

    def _mark_tree_as_solved(self) -> None:
        parent = self.parent
        while parent:
            parent._is_solved = True
            parent = parent.parent


class TreeState(TypedDict):
    root: Node
    input: str


def _summarize_message(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, list):
        content = " ".join(
            chunk.get("text", "") for chunk in content if isinstance(chunk, dict)
        )
    snippet = str(content)
    snippet = snippet.replace("\n", " ").strip()
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return f"{msg.__class__.__name__}({snippet})"


def _summarize_value(value: object) -> object:
    if isinstance(value, Node):
        return {
            "depth": value.depth,
            "height": value.height,
            "visits": value.visits,
            "children": len(value.children),
            "solved": value.is_solved,
            "score": round(value.value, 3),
        }
    if isinstance(value, BaseMessage):
        return _summarize_message(value)
    if isinstance(value, Reflection):
        return {"score": value.score, "found_solution": value.found_solution}
    if isinstance(value, dict):
        return {k: _summarize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        preview = [_summarize_value(v) for v in value[:3]]
        if len(value) > 3:
            preview.append("...")
        return preview
    return value


def _summarize_event(event: Dict[str, object]) -> str:
    parts = []
    for node, payload in event.items():
        parts.append(f"{node}: {_summarize_value(payload)}")
    return " | ".join(parts)


def select(root: Node) -> Node:
    if not root.children:
        return root
    node = root
    while node.children:
        node = max(node.children, key=lambda child: child.upper_confidence_bound())
    return node


def build_graph(
    model: str,
    temperature: float,
    tavily_max_results: int,
    max_depth: int,
) -> StateGraph:
    llm = ChatOpenAI(model=model, temperature=temperature)
    search = TavilySearchAPIWrapper()
    tavily_tool = TavilySearchResults(api_wrapper=search, max_results=tavily_max_results)
    tool_node = ToolNode(tools=[tavily_tool])
    tools = [tavily_tool]
    component_tracer = trace.get_tracer(APP_NAME)

    def _invoke_tavily(payload, invoke_config):
        """Forward LangGraph config into Tavily tool node for OTEL spans."""

        return tool_node.invoke(payload, config=invoke_config)
    # Follow otel_span_template guidance: gen_ai.operation.name identifies call_llm,
    # execute_tool, or invoke_agent for every span we emit.

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Reflect and grade the assistant response to the user question."),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="candidate"),
        ]
    )

    reflection_llm_chain = (
        prompt
        | llm.bind_tools(tools=[Reflection], tool_choice="Reflection").with_config(
            run_name="Reflection"
        )
        | PydanticToolsParser(tools=[Reflection])
    )

    @as_runnable
    def reflection_chain(inputs, config: RunnableConfig | None = None) -> Reflection:
        tool_choices = reflection_llm_chain.invoke(inputs, config=config)
        reflection = tool_choices[0]
        if not isinstance(inputs["candidate"][-1], AIMessage):
            reflection.found_solution = False
        return reflection

    prompt_template = ChatPromptTemplate.from_messages(
        [
            ("system", "You are an AI assistant."),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="messages", optional=True),
        ]
    )
    initial_answer_chain = prompt_template | llm.bind_tools(tools=tools).with_config(
        run_name="GenerateInitialCandidate"
    )
    parser = JsonOutputToolsParser(return_id=True)

    def generate_initial_response(state: TreeState, config: RunnableConfig) -> Dict[str, object]:
        def _invoke_initial(updated_config: RunnableConfig | None):
            return initial_answer_chain.invoke({"input": state["input"]}, config=updated_config)

        res = run_llm_with_span(
            component_tracer,
            "lats.call_llm.initial_response",
            agent_name=f"{APP_NAME}.llm",
            phase="initial_response",
            config=config,
            invoke_fn=_invoke_initial,
            extra_attributes={
                "lats.phase": "initial_response",
                "gen_ai.system": GEN_AI_SYSTEM,
                "gen_ai.request.model": model,
            },
        )
        parsed = parser.invoke(res)

        tool_messages: List[dict] = []
        for r in parsed:
            tool_payload = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": r["type"], "args": r["args"], "id": r["id"]}
                        ],
                    )
                ]
            }
            result = run_tool_with_span(
                component_tracer,
                "lats.execute_tool.tavily",
                agent_name=f"{APP_NAME}.tool.tavily",
                tool_name="tavily_search",
                payload=tool_payload,
                invoke_fn=_invoke_tavily,
                config=config,
                extra_attributes={
                    "gen_ai.tool.name": "tavily_search",
                    "gen_ai.tool.type": "FunctionTool",
                    "gen_ai.system": "tavily",
                },
            )
            tool_messages.append(result)

        output_messages = [res] + [tr["messages"][0] for tr in tool_messages]
        def _invoke_reflection(updated_config: RunnableConfig | None):
            return reflection_chain.invoke(
                {"input": state["input"], "candidate": output_messages},
                config=updated_config,
            )

        reflection = run_llm_with_span(
            component_tracer,
            "lats.call_llm.reflection",
            agent_name=f"{APP_NAME}.llm",
            phase="reflection",
            config=config,
            invoke_fn=_invoke_reflection,
            extra_attributes={
                "lats.phase": "reflection",
                "gen_ai.system": GEN_AI_SYSTEM,
                "gen_ai.request.model": model,
            },
        )
        return {
            **state,
            "root": Node(output_messages, reflection=reflection),
        }

    def generate_candidates(messages: ChatPromptValue, config: RunnableConfig):
        bound_kwargs = llm.bind_tools(tools=tools).kwargs

        def _invoke_expand(updated_config: RunnableConfig | None):
            callbacks = (updated_config or {}).get("callbacks")
            n_value = (updated_config or {}).get("configurable", {}).get("N", 5)
            return llm.generate(
                [messages.to_messages()],
                n=n_value,
                callbacks=callbacks,
                run_name="GenerateCandidates",
                **bound_kwargs,
            )

        chat_result = run_llm_with_span(
            component_tracer,
            "lats.call_llm.expand",
            agent_name=f"{APP_NAME}.llm",
            phase="expand",
            config=config,
            invoke_fn=_invoke_expand,
            extra_attributes={
                "lats.phase": "expand",
                "gen_ai.system": GEN_AI_SYSTEM,
                "gen_ai.request.model": model,
            },
        )
        return [gen.message for gen in chat_result.generations[0]]

    expansion_chain = prompt_template | generate_candidates

    def expand(state: TreeState, config: RunnableConfig) -> TreeState:
        root = state["root"]
        best_candidate = select(root)
        messages = best_candidate.get_trajectory()
        new_candidates = expansion_chain.invoke(
            {"input": state["input"], "messages": messages},
            config,
        )
        parsed = parser.batch(new_candidates)
        flattened = [
            (i, tool_call)
            for i, tool_calls in enumerate(parsed)
            for tool_call in tool_calls
        ]
        tool_responses = []
        for i, tool_call in flattened:
            tool_payload = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": tool_call["type"],
                                "args": tool_call["args"],
                                "id": tool_call["id"],
                            }
                        ],
                    )
                ]
            }
            result = run_tool_with_span(
                component_tracer,
                "lats.execute_tool.tavily",
                agent_name=f"{APP_NAME}.tool.tavily",
                tool_name="tavily_search",
                payload=tool_payload,
                invoke_fn=_invoke_tavily,
                config=config,
                extra_attributes={
                    "gen_ai.tool.name": "tavily_search",
                    "gen_ai.tool.type": "FunctionTool",
                    "gen_ai.system": "tavily",
                },
            )
            tool_responses.append((i, result))
        collected_responses: Dict[int, List[ToolMessage]] = defaultdict(list)
        for i, resp in tool_responses:
            collected_responses[i].append(resp["messages"][0])
        output_messages: List[List[BaseMessage]] = []
        for i, candidate in enumerate(new_candidates):
            output_messages.append([candidate] + collected_responses[i])

        reflection_inputs = [
            {"input": state["input"], "candidate": msgs} for msgs in output_messages
        ]

        def _invoke_reflection_batch(updated_config: RunnableConfig | None):
            return reflection_chain.batch(reflection_inputs, updated_config)

        reflections = run_llm_with_span(
            component_tracer,
            "lats.call_llm.reflection.batch",
            agent_name=f"{APP_NAME}.llm",
            phase="reflection",
            config=config,
            invoke_fn=_invoke_reflection_batch,
            extra_attributes={
                "lats.phase": "reflection",
                "gen_ai.system": GEN_AI_SYSTEM,
                "gen_ai.request.model": model,
                "langgraph.batch_size": len(reflection_inputs),
            },
        )
        child_nodes = [
            Node(messages, parent=best_candidate, reflection=reflection)
            for messages, reflection in zip(output_messages, reflections)
        ]
        best_candidate.children.extend(child_nodes)
        return state

    def should_loop(state: TreeState):
        root = state["root"]
        if root.is_solved:
            return END
        if root.height >= max_depth:
            return END
        return "expand"

    builder = StateGraph(TreeState)
    builder.add_node("start", generate_initial_response)
    builder.add_node("expand", expand)
    builder.add_edge(START, "start")
    builder.add_conditional_edges(
        "start",
        should_loop,
        ["expand", END],
    )
    builder.add_conditional_edges(
        "expand",
        should_loop,
        ["expand", END],
    )
    return builder.compile()


def load_questions(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        sample = handle.read().strip()
    if not sample:
        return []
    handle_lines = sample.splitlines()
    reader = csv.DictReader(handle_lines)
    questions: List[str] = []
    if reader.fieldnames and "question" in [col.lower() for col in reader.fieldnames]:
        question_col = next(
            column for column in reader.fieldnames if column.lower() == "question"
        )
        for row in reader:
            value = row.get(question_col)
            if value:
                questions.append(value.strip())
        if questions:
            return questions
    # Fallback to plain text (one question per line)
    return [line.strip() for line in handle_lines if line.strip()]


@dataclass
class RunResult:
    index: int
    question: str
    solved: bool
    height: int
    visits: int
    duration: float
    best_response: Optional[str]
    reflection_score: Optional[float]
    events: List[str]
    error: Optional[str] = None

    def to_metadata(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "question": self.question,
            "solved": self.solved,
            "height": self.height,
            "visits": self.visits,
            "duration_seconds": self.duration,
            "best_response": self.best_response,
            "reflection_score": self.reflection_score,
            "error": self.error,
        }


def run_question(
    graph: StateGraph,
    question: str,
    index: int,
    branching_factor: int,
    tracer: trace.Tracer | None = None,
) -> RunResult:
    events: List[str] = []
    usage_callback = LangChainUsageCallback()
    config = {
        "configurable": {"N": branching_factor},
        "callbacks": [usage_callback],
    }
    start = time.perf_counter()
    root_node: Optional[Node] = None
    span_attrs = {
        "question_index": index,
        "branching_factor": branching_factor,
        "question": question,
    }
    span_context = (
        invoke_agent_span(
            tracer,
            "lats.question",
            agent_name=f"{APP_NAME}.question",
            payload=question,
            in_process_call=True,
            extra_attributes=span_attrs,
        )
        if tracer
        else nullcontext((None, 0))
    )
    with span_context as (span, question_input_bytes):
        try:
            for event in graph.stream({"input": question}, config=config):
                summary = _summarize_event(event)
                events.append(summary)
                for node_name, payload in event.items():
                    node_attrs = {"node": node_name}
                    node_span_cm = (
                        invoke_agent_span(
                            tracer,
                            f"lats.node.{node_name}",
                            agent_name=f"{APP_NAME}.node.{node_name}",
                            payload=payload,
                            in_process_call=True,
                            extra_attributes=node_attrs,
                        )
                        if tracer
                        else nullcontext((None, 0))
                    )
                    with node_span_cm as (_node_span, _):
                        if isinstance(payload, dict):
                            maybe_root = payload.get("root")
                            if isinstance(maybe_root, Node):
                                root_node = maybe_root
                print(f"  {summary}", flush=True)
        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error("Graph execution failed for question %s: %s", index, exc)
            record_usage_on_span(span, usage_callback)
            return RunResult(
                index=index,
                question=question,
                solved=False,
                height=0,
                visits=0,
                duration=duration,
                best_response=None,
                reflection_score=None,
                events=events,
                error=str(exc),
            )

        duration = time.perf_counter() - start
        if not root_node:
            events.append("Graph returned no root node.")
            record_usage_on_span(span, usage_callback)
            return RunResult(
                index=index,
                question=question,
                solved=False,
                height=0,
                visits=0,
                duration=duration,
                best_response=None,
                reflection_score=None,
                events=events,
                error="missing-root",
            )
        solution_node = root_node.get_best_solution()
        trajectory = solution_node.get_trajectory(include_reflections=False)
        last_message = trajectory[-1] if trajectory else None
        best_response = None
        if isinstance(last_message, (AIMessage, ToolMessage, HumanMessage)):
            best_response = (
                last_message.content
                if isinstance(last_message.content, str)
                else str(last_message.content)
            )
        solved = bool(solution_node.is_solved)
        events.append(
            f"Final height={root_node.height}, visits={root_node.visits}, "
            f"node_value={solution_node.value:.3f}, solved={solved}"
        )
        if span and best_response is not None:
            record_invoke_agent_output(span, best_response, question_input_bytes)
        record_usage_on_span(span, usage_callback)
        return RunResult(
            index=index,
            question=question,
            solved=solved,
            height=root_node.height,
            visits=root_node.visits,
            duration=duration,
            best_response=best_response,
            reflection_score=solution_node.reflection.score if solution_node.reflection else None,
            events=events,
        )


def write_run_artifacts(
    results: List[RunResult],
    args: argparse.Namespace,
    dataset_source: str,
    run_id: str,
    trace_log_path: Optional[Path],
    metrics_log_path: Optional[Path],
) -> None:
    LOG_DIR.mkdir(exist_ok=True, parents=True)
    log_path = LOG_DIR / f"run_{run_id}.log"
    metadata_path = LOG_DIR / f"run_{run_id}.metadata.json"

    with log_path.open("w", encoding="utf-8") as handle:
        for result in results:
            status = "solved" if result.solved else "failed"
            handle.write(
                f"[{result.index}] {status} height={result.height} "
                f"visits={result.visits} duration={result.duration:.2f}s\n"
            )
            for event in result.events:
                handle.write(f"  {event}\n")

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
        "max_depth": args.max_depth,
        "branching_factor": args.branching_factor,
        "tavily_max_results": args.tavily_max_results,
        "questions": [result.to_metadata() for result in results],
    }
    if trace_log_path:
        try:
            rel_path = os.path.relpath(trace_log_path, start=BENCHMARK_ROOT)
        except ValueError:
            rel_path = str(trace_log_path)
        metadata["trace_log"] = rel_path
        metadata["trace_log_basename"] = trace_log_path.name
    if metrics_log_path:
        try:
            rel_metrics = os.path.relpath(metrics_log_path, start=BENCHMARK_ROOT)
        except ValueError:
            rel_metrics = str(metrics_log_path)
        metadata["metrics_log"] = rel_metrics
        metadata["metrics_log_basename"] = metrics_log_path.name
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Wrote %s and %s", log_path.name, metadata_path.name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Language Agent Tree Search benchmark runner."
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
        help="Sampling temperature applied to the LATS chains.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum rollout height before the search stops.",
    )
    parser.add_argument(
        "--branching-factor",
        type=int,
        default=5,
        help="Number of candidate continuations sampled per expansion.",
    )
    parser.add_argument(
        "--questions-file",
        default=str(DEFAULT_DATASET),
        help="CSV or plaintext file containing questions to benchmark.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="0-based index into the dataset to start from.",
    )
    parser.add_argument(
        "--num-questions",
        type=int,
        default=1,
        help="Number of questions to run sequentially.",
    )
    parser.add_argument(
        "--tavily-max-results",
        type=int,
        default=5,
        help="Maximum Tavily search results returned per tool invocation.",
    )
    parser.add_argument(
        "--metrics-interval",
        type=float,
        default=DEFAULT_METRICS_INTERVAL,
        help=(
            "Seconds between psutil samples for system metrics "
            f"(default {DEFAULT_METRICS_INTERVAL}, override via LATS_METRICS_INTERVAL_SECONDS)."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tracer: Optional[trace.Tracer] = None
    trace_log_path: Optional[Path] = None
    provider: Optional[TracerProvider] = None
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
        logger.info("OpenTelemetry trace log: %s", trace_log_path)
    except Exception as exc:  # pragma: no cover - tracing is optional
        logger.warning("Unable to initialize OpenTelemetry tracing: %s", exc)
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
            logger=logger,
            interval_seconds=max(1.0, args.metrics_interval),
        )
        metrics_log_path = metrics_recorder.output_path
        metrics_recorder.start()
        logger.info("System metrics log: %s", metrics_log_path)
    except Exception as exc:  # pragma: no cover - metrics optional
        logger.warning("Unable to initialize system metrics recorder: %s", exc)
        metrics_recorder = None
        metrics_log_path = None
    try:
        dataset_path = Path(args.questions_file)
        questions = load_questions(dataset_path)
        if not questions:
            raise SystemExit(f"No questions found in {dataset_path}")
        if args.start_index >= len(questions):
            raise SystemExit("Start index exceeds dataset length.")
        end = min(len(questions), args.start_index + args.num_questions)
        selected = [
            (i, question)
            for i, question in enumerate(questions[args.start_index:end], start=args.start_index)
        ]
        graph = build_graph(
            model=args.model,
            temperature=args.temperature,
            tavily_max_results=args.tavily_max_results,
            max_depth=max(args.max_depth, 1),
        )

        results: List[RunResult] = []
        for dataset_index, question in selected:
            logger.info("Running question %s: %s", dataset_index, question)
            result = run_question(
                graph=graph,
                question=question,
                index=dataset_index,
                branching_factor=args.branching_factor,
                tracer=tracer,
            )
            results.append(result)

        write_run_artifacts(
            results,
            args,
            dataset_source=str(dataset_path),
            run_id=run_id,
            trace_log_path=trace_log_path,
            metrics_log_path=metrics_log_path,
        )
    finally:
        if metrics_recorder:
            metrics_recorder.stop()
        if provider:
            provider.shutdown()


if __name__ == "__main__":
    main()
