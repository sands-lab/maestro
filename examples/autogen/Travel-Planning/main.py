import asyncio
import os
import sys

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_core import SingleThreadedAgentRuntime

# Get the absolute path of the current script
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory (autogen-examples)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to sys.path
sys.path.append(parent_dir)

# Import our custom AutoGen exporters
from autogen_ext.models.openai import OpenAIChatCompletionClient
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor

from autogen_exporters import setup_autogen_metrics, setup_autogen_tracing

# ==========================================================
# Configure AutoGen-compatible exporters (JSON files and SigNoz)
# ==========================================================

# Set up AutoGen-compatible tracing; export to local JSON and SigNoz
tracer_provider = setup_autogen_tracing(
    service_name="autogen-multi-agent-system",
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Set up AutoGen-compatible metrics; export to local JSON and SigNoz
meter_provider = setup_autogen_metrics(
    service_name="autogen-multi-agent-system",
    enable_system_metrics=True,
    enable_signoz=True,
    signoz_endpoint="http://localhost:4317",
)

# Auto-collect CPU usage, memory usage, network I/O, etc.
SystemMetricsInstrumentor().instrument()

# Auto-collect OpenAI request token usage and latency
OpenAIInstrumentor().instrument()

# Get tracer and meter for manual instrumentation
tracer = trace.get_tracer("autogen-custom-tracer")
meter = metrics.get_meter("autogen-custom-metrics")

# Define a histogram to track message size distribution
message_size_histogram = meter.create_histogram(
    name="autogen.agent.message_size",
    description="Size of messages processed by AutoGen agents",
    unit="bytes",
)


async def amain():
    model_client = OpenAIChatCompletionClient(model="gpt-5")
    with tracer.start_as_current_span("run_team_session"):
        planner_agent = AssistantAgent(
            "planner_agent",
            model_client=model_client,
            description="A helpful assistant that can plan trips.",
            system_message="You are a helpful assistant that can suggest a travel plan for a user based on their request.",
        )

        local_agent = AssistantAgent(
            "local_agent",
            model_client=model_client,
            description="A local assistant that can suggest local activities or places to visit.",
            system_message="You are a helpful assistant that can suggest authentic and interesting local activities or places to visit for a user and can utilize any context information provided.",
        )

        language_agent = AssistantAgent(
            "language_agent",
            model_client=model_client,
            description="A helpful assistant that can provide language tips for a given destination.",
            system_message="You are a helpful assistant that can review travel plans, providing feedback on important/critical tips about how best to address language or communication challenges for the given destination. If the plan already includes language tips, you can mention that the plan is satisfactory, with rationale.",
        )

        travel_summary_agent = AssistantAgent(
            "travel_summary_agent",
            model_client=model_client,
            description="A helpful assistant that can summarize the travel plan.",
            system_message="You are a helpful assistant that can take in all of the suggestions and advice from the other agents and provide a detailed final travel plan. You must ensure that the final plan is integrated and complete. YOUR FINAL RESPONSE MUST BE THE COMPLETE PLAN. When the plan is complete and all perspectives are integrated, you can respond with TERMINATE.",
        )

        runtime = SingleThreadedAgentRuntime()
        runtime.start()

        termination = TextMentionTermination("TERMINATE")
        group_chat = RoundRobinGroupChat(
            [planner_agent, local_agent, language_agent, travel_summary_agent],
            termination_condition=termination,
        )
        await Console(group_chat.run_stream(task="Plan a 3 day trip to Nepal."))

    await model_client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(amain())
