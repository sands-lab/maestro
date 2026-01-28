"""
Coordinator API Server (without Gradio UI)
A2A-compatible HTTP API server for the coordinator agent
"""

import logging
import os
import sys
from pathlib import Path
import click
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Part,
    TaskState,
    TextPart,
)
from a2a.utils import new_agent_text_message
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from .agent import root_agent as coordinator
from dotenv import load_dotenv

# Add shared directory to path to import telemetry_setup
# Coordinator is at: src/hosts/coordinator/coordinator/__main_api__.py
# Shared is at: src/agents/shared/telemetry_setup.py
# In Docker: /app/shared/telemetry_setup.py
shared_path = Path(__file__).parent.parent.parent.parent / "agents" / "shared"
# Also check /app/shared (Docker container path)
if not shared_path.exists():
    shared_path = Path("/app/shared")
sys.path.insert(0, str(shared_path))
try:
    from telemetry_setup import setup_tracing, setup_metrics
except ImportError:
    # Fallback if telemetry_setup is not available
    def setup_tracing(*args, **kwargs):
        pass
    def setup_metrics(*args, **kwargs):
        pass

load_dotenv()

# Initialize OpenTelemetry tracing
setup_tracing(service_name="coordinator")

# Initialize OpenTelemetry metrics
setup_metrics(service_name="coordinator")

APP_NAME = 'coordinator_app'
SESSION_SERVICE = InMemorySessionService()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ADKAgentExecutor(AgentExecutor):
    """Executor that wraps Google ADK Agent for A2A protocol"""

    def __init__(
        self,
        agent,
        status_message="Processing request...",
        artifact_name="response",
    ):
        """Initialize a generic ADK agent executor.

        Args:
            agent: The ADK agent instance
            status_message: Message to display while processing
            artifact_name: Name for the response artifact
        """
        self.agent = agent
        self.status_message = status_message
        self.artifact_name = artifact_name
        self.runner = Runner(
            app_name=agent.name,
            agent=agent,
            artifact_service=InMemoryArtifactService(),
            session_service=SESSION_SERVICE,
            memory_service=InMemoryMemoryService(),
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Cancel the execution of a specific task."""
        raise NotImplementedError(
            "Cancellation is not implemented for ADKAgentExecutor."
        )

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        from a2a.utils import new_task
        from google.genai import types

        query = context.get_user_input()
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        if context.call_context:
            user_id = context.call_context.user.user_name
        else:
            user_id = "a2a_user"

        try:
            # Update status with custom message
            await updater.update_status(
                TaskState.working,
                new_agent_text_message(self.status_message, task.context_id, task.id),
            )

            # Process with ADK agent
            session = await self.runner.session_service.create_session(
                app_name=self.agent.name,
                user_id=user_id,
                state={},
                session_id=task.context_id,
            )

            content = types.Content(
                role="user", parts=[types.Part.from_text(text=query)]
            )

            response_text = ""
            async for event in self.runner.run_async(
                user_id=user_id, session_id=session.id, new_message=content
            ):
                # Extract text from function responses (editor's response is here)
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "function_response"):
                            # Extract text from function response
                            # The function_response contains the Task returned from send_message
                            try:
                                func_resp = part.function_response
                                resp_data = None
                                if hasattr(func_resp, "response"):
                                    response_content = func_resp.response
                                    if isinstance(response_content, dict) and 'response' in response_content:
                                        resp_data = response_content['response']
                                    elif isinstance(response_content, dict) and 'result' in response_content:
                                        resp_data = response_content['result']
                                    else:
                                        resp_data = response_content
                                else:
                                    resp_data = func_resp

                                # Handle case where resp_data might be a string representation
                                if isinstance(resp_data, str):
                                    # Try to extract #IamEditor from string representation
                                    if "#IamEditor" in resp_data:
                                        # Extract the text containing #IamEditor
                                        # Look for text='...' patterns in the string
                                        import re
                                        # Try multiple patterns to find text with #IamEditor
                                        # Pattern 1: text='...' (single quotes, may have escaped quotes)
                                        # Pattern 2: text="..." (double quotes, may have escaped quotes)
                                        # Pattern 3: text=... (no quotes, ends with \n or ')
                                        patterns = [
                                            r"text=['\"]((?:[^'\"\\]|\\.|(?:\\\\n)|(?:\\\\'))*?#IamEditor.*?)(?:['\"]|\\n|$)",
                                            r"text=((?:[^'\"\\]|\\.|(?:\\\\n))*?#IamEditor.*?)(?:['\"]|\\n|$)",
                                        ]
                                        text_matches = []
                                        for pattern in patterns:
                                            matches = re.findall(pattern, resp_data, re.DOTALL)
                                            if matches:
                                                text_matches.extend(matches)
                                                break

                                        if not text_matches:
                                            # Fallback: find #IamEditor and extract surrounding text
                                            iam_pos = resp_data.find('#IamEditor')
                                            if iam_pos > 0:
                                                # Look backwards for text= or text=' or text="
                                                start_patterns = [r"text=['\"]", r'text="', r"text='"]
                                                start_pos = -1
                                                for pattern in start_patterns:
                                                    matches = list(re.finditer(pattern, resp_data[:iam_pos]))
                                                    if matches:
                                                        start_pos = matches[-1].end()
                                                        break

                                                if start_pos > 0:
                                                    # Look forward for closing quote or end
                                                    end_pos = resp_data.find("'", iam_pos)
                                                    if end_pos == -1:
                                                        end_pos = resp_data.find('"', iam_pos)
                                                    if end_pos == -1:
                                                        end_pos = resp_data.find('\\n', iam_pos)
                                                    if end_pos == -1:
                                                        end_pos = len(resp_data)

                                                    text_match = resp_data[start_pos:end_pos]
                                                    text_matches.append(text_match)

                                        if text_matches:
                                            # Use the last match (should be the final editor response)
                                            for match in reversed(text_matches):
                                                # Unescape the string
                                                text = match.replace('\\n', '\n').replace('\\t', '\t').replace("\\'", "'").replace('\\"', '"').replace('\\\\', '\\')
                                                if "#IamEditor" in text:
                                                    response_text = text
                                                    logger.info(f"Extracted text with #IamEditor from string representation (length: {len(text)})")
                                                    break

                                # Extract text from Task.artifacts (object format)
                                if hasattr(resp_data, "artifacts"):
                                    artifacts = resp_data.artifacts
                                    if artifacts:
                                        for artifact in artifacts:
                                            if hasattr(artifact, "parts"):
                                                parts = artifact.parts
                                                if parts:
                                                    for art_part in parts:
                                                        text = None
                                                        if hasattr(art_part, "root") and hasattr(art_part.root, "text"):
                                                            text = art_part.root.text
                                                        elif hasattr(art_part, "text"):
                                                            text = art_part.text

                                                        if text and isinstance(text, str):
                                                            # If contains #IamEditor, use it as final response
                                                            if "#IamEditor" in text:
                                                                response_text = text
                                                                break
                                                            # Otherwise, append to response
                                                            elif not response_text or "#IamEditor" not in response_text:
                                                                response_text += text + "\n"
                                                    if response_text and "#IamEditor" in response_text:
                                                        break
                                            if response_text and "#IamEditor" in response_text:
                                                break
                            except Exception as e:
                                logger.warning(f"Error extracting function response: {e}", exc_info=True)
                        elif hasattr(part, "function_call"):
                            pass  # Function calls are handled internally by ADK

                # Also check final response
                if event.is_final_response() and event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            text = part.text
                            if text not in response_text:  # Avoid duplicates
                                response_text += text + "\n"

            # Add response as artifact with custom name
            await updater.add_artifact(
                [Part(root=TextPart(text=response_text))],
                name=self.artifact_name,
            )

            await updater.complete()

        except Exception as e:
            await updater.update_status(
                TaskState.failed,
                new_agent_text_message(f"Error: {e!s}", task.context_id, task.id),
                final=True,
            )


@click.command()
@click.option('--host', default='0.0.0.0', help='Host to bind to')
@click.option('--port', default=8093, help='Port to bind to')
def main(host: str, port: int):
    """Start the Coordinator API server"""

    logger.info(f"Starting Coordinator API server on {host}:{port}")

    # Create agent card
    agent_card = AgentCard(
        name='Coordinator Agent',
        description=coordinator.description or "Coordinator agent for content creation",
        url=f'http://{host}:{port}',
        version="1.0.0",
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(
            streaming=False,
        ),
        skills=[
            AgentSkill(
                id='coordinate_content_creation',
                name='coordinate_content_creation',
                description='Coordinates multiple agents to create content',
                tags=['coordination', 'orchestration'],
            ),
        ],
    )

    # Create task store and executor
    task_store = InMemoryTaskStore()
    agent_executor = ADKAgentExecutor(
        agent=coordinator,
    )

    # Create request handler
    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=task_store,
    )

    # Create A2A application
    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    # Build the ASGI app
    asgi_app = app.build()

    logger.info(f"Coordinator API server ready at http://{host}:{port}")

    # Run server with the ASGI app
    uvicorn.run(
        asgi_app,
        host=host,
        port=port,
        log_level='info',
    )


if __name__ == '__main__':
    main()
