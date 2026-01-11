import logging
import sys
from pathlib import Path

import click
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from .agent import root_agent as writer
from .agent_executor import ADKAgentExecutor
from dotenv import load_dotenv

# Add shared directory to path to import telemetry_setup
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "shared"))
from telemetry_setup import setup_tracing, setup_metrics

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenTelemetry tracing
setup_tracing(service_name="content-writer")

# Initialize OpenTelemetry metrics
setup_metrics(service_name="content-writer")


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option("--host", default="localhost")
@click.option("--port", default=10002)
def main(host, port):

    # Agent card (metadata)
    agent_card = AgentCard(
        name='Content Writer Agent',
        description=writer.description,
        url=f'http://{host}:{port}',
        version="1.0.0",
        defaultInputModes=["text", "text/plain"],
        defaultOutputModes=["text", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="content_writer",
                name="Writes content",
                description="Writes content based on a provided plan or simple story abstraction.",
                tags=["write", "content"],
                examples=[
                    "Write a blog post about the benefits of learning Java.",
                ],
            )
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=ADKAgentExecutor(
            agent=writer,
        ),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    uvicorn.run(server.build(), host=host, port=port)


if __name__ == "__main__":
    main()
