import asyncio
import os
import sys
from typing import Any, Dict, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import Swarm
from autogen_agentchat.ui import Console

# Get the absolute path of the current script
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (autogen-examples)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to sys.path
sys.path.append(parent_dir)

# Import our custom AutoGen exporters
from autogen_exporters import setup_autogen_metrics, setup_autogen_tracing
from autogen_ext.models.openai import OpenAIChatCompletionClient
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

# ==========================================================
# Configure AutoGen-compatible exporters (JSON files and SigNoz)
# ==========================================================

# Set up AutoGen-compatible tracing; export to local JSON and SigNoz
tracer_provider = setup_autogen_tracing(
    service_name="autogen-stock-research-system",
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Set up AutoGen-compatible metrics; export to local JSON and SigNoz
meter_provider = setup_autogen_metrics(
    service_name="autogen-stock-research-system",
    enable_system_metrics=True,
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Auto-collect CPU usage, memory usage, network I/O, etc.
SystemMetricsInstrumentor().instrument()

# Auto-collect OpenAI request token usage and latency
OpenAIInstrumentor().instrument()

# Get tracer and meter for manual instrumentation
tracer = trace.get_tracer("stock-research-custom-tracer")
meter = metrics.get_meter("stock-research-custom-metrics")

# Define a histogram to track message size distribution
message_size_histogram = meter.create_histogram(
    name="autogen.agent.message_size",
    description="Size of messages processed by AutoGen agents",
    unit="bytes",
)

# Define a counter to track research tasks
research_task_counter = meter.create_counter(
    name="stock_research.tasks.count",
    description="Number of stock research tasks executed",
)

# Define a histogram to track task duration
task_duration_histogram = meter.create_histogram(
    name="stock_research.task.duration",
    description="Duration of stock research task execution",
    unit="seconds",
)

# Define a counter to track tool calls
tool_call_counter = meter.create_counter(
    name="stock_research.tools.calls",
    description="Number of tool calls made during research",
)

# Define a counter to track agent handoffs
agent_handoff_counter = meter.create_counter(
    name="stock_research.agent.handoffs",
    description="Number of agent handoffs during research",
)

async def get_stock_data(symbol: str) -> Dict[str, Any]:
    """Get stock market data for a given symbol"""
    with tracer.start_as_current_span("get_stock_data") as span:
        span.set_attribute("tool.name", "get_stock_data")
        span.set_attribute("stock.symbol", symbol)

        # Record tool call
        tool_call_counter.add(1, {"tool": "get_stock_data", "symbol": symbol})

        # Simulate data retrieval
        data = {
            "price": 180.25,
            "volume": 1000000,
            "pe_ratio": 65.4,
            "market_cap": "700B",
        }

        # Record response size
        response_size = len(str(data).encode("utf-8"))
        message_size_histogram.record(
            response_size, {"message_type": "tool_response", "tool": "get_stock_data"}
        )
        span.set_attribute("response.size", response_size)

        return data


async def get_news(query: str) -> List[Dict[str, str]]:
    """Get recent news articles about a company"""
    with tracer.start_as_current_span("get_news") as span:
        span.set_attribute("tool.name", "get_news")
        span.set_attribute("news.query", query)

        # Record tool call
        tool_call_counter.add(1, {"tool": "get_news", "query": query})

        # Simulate news data
        news_data = [
            {
                "title": "Tesla Expands Cybertruck Production",
                "date": "2024-03-20",
                "summary": "Tesla ramps up Cybertruck manufacturing capacity at Gigafactory Texas, aiming to meet strong demand.",
            },
            {
                "title": "Tesla FSD Beta Shows Promise",
                "date": "2024-03-19",
                "summary": "Latest Full Self-Driving beta demonstrates significant improvements in urban navigation and safety features.",
            },
            {
                "title": "Model Y Dominates Global EV Sales",
                "date": "2024-03-18",
                "summary": "Tesla's Model Y becomes best-selling electric vehicle worldwide, capturing significant market share.",
            },
        ]

        # Record response size
        response_size = len(str(news_data).encode("utf-8"))
        message_size_histogram.record(
            response_size, {"message_type": "tool_response", "tool": "get_news"}
        )
        span.set_attribute("response.size", response_size)
        span.set_attribute("news.count", len(news_data))

        return news_data


# model_client = OpenAIChatCompletionClient(
#     model="gpt-5.1",
#     model_info={
#         "vision":True,
#         "function_calling":True,
#         "json_output":True,
#         "family":"gpt-5",
#         "structured_output":False
#     }
# )

model_client = OpenAIChatCompletionClient(
    model="gpt-5-mini"
)

planner = AssistantAgent(
    "planner",
    model_client=model_client,
    handoffs=["financial_analyst", "news_analyst", "writer"],
    system_message="""You are a research planning coordinator.
    Coordinate market research by delegating to specialized agents:
    - Financial Analyst: For stock data analysis
    - News Analyst: For news gathering and analysis
    - Writer: For compiling final report
    Always send your plan first, then handoff to appropriate agent.
    Always handoff to a single agent at a time.
    Use TERMINATE when research is complete.""",
)

financial_analyst = AssistantAgent(
    "financial_analyst",
    model_client=model_client,
    handoffs=["planner"],
    tools=[get_stock_data],
    system_message="""You are a financial analyst.
    Analyze stock market data using the get_stock_data tool.
    Provide insights on financial metrics.
    Always handoff back to planner when analysis is complete.""",
)

news_analyst = AssistantAgent(
    "news_analyst",
    model_client=model_client,
    handoffs=["planner"],
    tools=[get_news],
    system_message="""You are a news analyst.
    Gather and analyze relevant news using the get_news tool.
    Summarize key market insights from news.
    Always handoff back to planner when analysis is complete.""",
)

writer = AssistantAgent(
    "writer",
    model_client=model_client,
    handoffs=["planner"],
    system_message="""You are a financial report writer.
    Compile research findings into clear, concise reports.
    Always handoff back to planner when writing is complete.""",
)


async def amain():
    with tracer.start_as_current_span("stock_research_session") as span:
        # Record research task start
        research_task_counter.add(1, {"task_type": "stock_research"})

        # Set span attributes
        span.set_attribute("research.type", "stock_research")
        span.set_attribute("model.name", "gpt-5-mini")
        span.set_attribute("team.type", "swarm")
        span.set_attribute("agents.count", 4)

        # Define termination condition
        text_termination = TextMentionTermination("TERMINATE")
        termination = text_termination

        with tracer.start_as_current_span("initialize_research_team"):
            research_team = Swarm(
                participants=[planner, financial_analyst, news_analyst, writer],
                termination_condition=termination,
            )

        task = "Conduct market research for TSLA stock"

        # Record task input size
        task_size = len(task.encode("utf-8"))
        message_size_histogram.record(task_size, {"message_type": "task_input"})
        span.set_attribute("task.input_size", task_size)
        span.set_attribute("task.content", task)

        with tracer.start_as_current_span("execute_research_task") as task_span:
            task_span.set_attribute("task.description", task)
            result = await Console(research_team.run_stream(task=task))

            # Record result size
            if result:
                result_size = len(str(result).encode("utf-8"))
                message_size_histogram.record(
                    result_size, {"message_type": "task_output"}
                )
                task_span.set_attribute("result.size", result_size)

        await model_client.close()

if __name__ == "__main__":
    asyncio.run(amain())
