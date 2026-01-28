# ruff: noqa: E501
# pylint: disable=logging-fstring-interpolation
import asyncio
import json
import os
import uuid
from typing import Any

import httpx
from dotenv import load_dotenv

from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    MessageSendParams,
    Part,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
)
from .remote_agent_connection import (
    RemoteAgentConnections,
    TaskUpdateCallback,
)
from google.adk import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext

from google.adk.models.lite_llm import LiteLlm
import litellm

litellm._turn_on_debug()
load_dotenv()


def convert_part(part: Part, tool_context: ToolContext):
    """Convert a part to text. Only text parts are supported."""
    if part.type == 'text':
        return part.text

    return f'Unknown type: {part.type}'


def convert_parts(parts: list[Part], tool_context: ToolContext):
    """Convert parts to text."""
    rval = []
    for p in parts:
        rval.append(convert_part(p, tool_context))
    return rval


def create_send_message_payload(
    text: str, task_id: str | None = None, context_id: str | None = None
) -> dict[str, Any]:
    """Helper function to create the payload for sending a task."""
    payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [{'type': 'text', 'text': text}],
            'messageId': uuid.uuid4().hex,
        },
    }

    if task_id:
        payload['message']['taskId'] = task_id

    if context_id:
        payload['message']['contextId'] = context_id
    return payload


class CoordinatorAgent:
    """The Coordinator agent.

    This is the agent responsible for sending tasks to agents.
    """

    def __init__(
        self,
        task_callback: TaskUpdateCallback | None = None,
    ) -> None:
        self.task_callback = task_callback
        self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
        self.cards: dict[str, AgentCard] = {}
        self.agents: str = ''

    async def _async_init_components(
        self, remote_agent_addresses: list[str]
    ) -> None:
        """Asynchronous part of initialization."""
        import asyncio

        # Use a single httpx.AsyncClient for all card resolutions for efficiency
        # Increased timeout from 30s to 60s for better reliability
        async with httpx.AsyncClient(timeout=60) as client:
            for address in remote_agent_addresses:
                card_resolver = A2ACardResolver(
                    client, address
                )  # Constructor is sync

                # Retry mechanism: up to 5 retries with 2 second intervals
                max_retries = 5
                retry_delay = 2.0
                card = None

                for attempt in range(max_retries):
                    try:
                        card = await card_resolver.get_agent_card()
                        break  # Successfully obtained, exit retry loop
                    except httpx.ConnectError as e:
                        if attempt < max_retries - 1:
                            print(
                                f'Retrying connection to {address} (attempt {attempt + 1}/{max_retries}): {e}'
                            )
                            await asyncio.sleep(retry_delay)
                        else:
                            print(
                                f'ERROR: Failed to get agent card from {address} after {max_retries} attempts: {e}'
                            )
                    except Exception as e:  # Catch other potential errors
                        if attempt < max_retries - 1:
                            print(
                                f'Retrying connection to {address} (attempt {attempt + 1}/{max_retries}): {e}'
                            )
                            await asyncio.sleep(retry_delay)
                        else:
                            print(
                                f'ERROR: Failed to initialize connection for {address} after {max_retries} attempts: {e}'
                            )

                # If card is successfully obtained, create connection
                if card:
                    try:
                        remote_connection = RemoteAgentConnections(
                            agent_card=card, agent_url=address
                        )
                        self.remote_agent_connections[card.name] = remote_connection
                        self.cards[card.name] = card
                        print(f'Successfully connected to agent: {card.name} at {address}')
                    except Exception as e:
                        print(
                            f'ERROR: Failed to create connection for {address}: {e}'
                        )

        # Populate self.agents using the logic from original __init__ (via list_remote_agents)
        agent_info = []
        for agent_detail_dict in self.list_remote_agents():
            agent_info.append(json.dumps(agent_detail_dict))
        self.agents = '\n'.join(agent_info)

    @classmethod
    async def create(
        cls,
        remote_agent_addresses: list[str],
        task_callback: TaskUpdateCallback | None = None,
    ) -> 'CoordinatorAgent':
        """Create and asynchronously initialize an instance of the CoordinatorAgent."""
        instance = cls(task_callback)
        await instance._async_init_components(remote_agent_addresses)
        return instance

    def create_agent(self) -> Agent:
        """Create an instance of the CoordinatorAgent."""
        model_id = 'gemini-2.5-flash'

        if os.getenv("PROVIDER") == "google":
            llm = "gemini-2.5-flash"
        elif os.getenv("PROVIDER") == "aliyun" or os.getenv("PROVIDER") == "ollama":
            if os.getenv("PROVIDER") == "aliyun":
                api_key = os.getenv("ALIYUN_API_KEY")
                api_base = os.getenv("API_BASE")
                model_name = os.getenv("MODEL", "qwen-plus")
            else:
                api_key = os.getenv("OLLAMA_API_KEY", "dummy")
                api_base = os.getenv("OLLAMA_API_BASE")
                # Use OpenAI-compatible format for Mock LLM (not Ollama format)
                model_name = os.getenv('MODEL', 'gpt-3.5-turbo')

            llm = LiteLlm(
                model=model_name,
                api_base=api_base,
                api_key=api_key,
                # Force OpenAI format for Mock LLM compatibility
                custom_llm_provider="openai" if os.getenv("PROVIDER") == "ollama" else None
            )
        else:
            raise ValueError("Unsupported PROVIDER. Please set PROVIDER to 'google', 'aliyun', or 'ollama'.")

        model_id = llm

        print(f'[Coordinator] Using model: {model_id}')
        if os.getenv("PROVIDER") == "ollama":
            print(f'[Coordinator] Provider: OpenAI-compatible (Mock LLM)')
            print(f'[Coordinator] API Base: {os.getenv("OLLAMA_API_BASE")}')
            print(f'[Coordinator] Model Name: {os.getenv("MODEL", "gpt-3.5-turbo")}')

        try:
            return Agent(
                model=model_id,
                name='Routing_agent',
                instruction=self.root_instruction,
                before_model_callback=self.before_model_callback,
                description=(
                    'This coordinator agent orchestrates the content planning and content writing agents'
                ),
                tools=[
                    self.send_message,
                ],
            )
        except Exception as e:
            print("=" * 80)
            print(f"❌ Error creating Agent: {type(e).__name__}")
            print(f"Error message: {str(e)}")
            if hasattr(e, '__cause__') and e.__cause__:
                print(f"Caused by: {type(e.__cause__).__name__}: {str(e.__cause__)}")
            print("=" * 80)
            raise

    def root_instruction(self, context: ReadonlyContext) -> str:
        """Generate the root instruction for the CoordinatorAgent."""
        current_agent = self.check_active_agent(context)
        return f"""
        **Role:** You are the central content coordination agent. Your primary function is to manage the content creation process.
        Upon receiving a high-level description of content from the user, you will perform the following tasks and then return the
        final polished content:

        Task 1. **Content Planning**
        Task 2. **Content Writing**
        Task 3. **Content Editing**

        **Core Directives:**

        * **Task Delegation:** Utilize the `send_message` function to assign each task to a remote agent.
        * **Contextual Awareness for Remote Agents:** If a remote agent repeatedly requests user confirmation, assume it lacks access to the full conversation history. In such cases, enrich the task description with all necessary contextual information relevant to that         specific agent.
        * **Autonomous Agent Engagement:** Never seek user permission before engaging with remote agents. If multiple agents are required to fulfill a request, connect with them directly without requesting user preference or confirmation.
        * **Transparent Communication:** Always present the complete and detailed response from the remote agent to the user.
        * **User Confirmation Relay:** If a remote agent asks for confirmation, and the user has not already provided it, relay this confirmation request to the user.
        * **Focused Information Sharing:** Provide remote agents with only relevant contextual information. Avoid extraneous details.
        * **No Redundant Confirmations:** Do not ask remote agents for confirmation of information or actions.
        * **Tool Reliance:** Strictly rely on available tools to address user requests. Do not generate responses based on assumptions. If information is insufficient, request clarification from the user.
        * **Prioritize Recent Interaction:** Focus primarily on the most recent parts of the conversation when processing requests.
        * **Active Agent Prioritization:** If an active agent is already engaged, route subsequent related requests to that agent using the appropriate task update tool.

        **Agent Roster:**

        * Available Agents: `{self.agents}`
        * Currently Active Agent: `{current_agent['active_agent']}`
                """

    def check_active_agent(self, context: ReadonlyContext):
        state = context.state
        if (
            'session_id' in state
            and 'session_active' in state
            and state['session_active']
            and 'active_agent' in state
        ):
            return {'active_agent': f'{state["active_agent"]}'}
        return {'active_agent': 'None'}

    def before_model_callback(
        self, callback_context: CallbackContext, llm_request
    ):
        state = callback_context.state
        if 'session_active' not in state or not state['session_active']:
            if 'session_id' not in state:
                state['session_id'] = str(uuid.uuid4())
            state['session_active'] = True

    def list_remote_agents(self):
        """List the available remote agents you can use to delegate the task."""
        if not self.cards:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            print(f'Found agent card: {card.model_dump(exclude_none=True)}')
            print('=' * 100)
            remote_agent_info.append(
                {'name': card.name, 'description': card.description}
            )
        return remote_agent_info

    async def send_message(
        self, agent_name: str, task: str, tool_context: ToolContext
    ):
        """Sends a task to remote agent.

        This will send a message to the remote agent named agent_name.

        Args:
            agent_name: The name of the agent to send the task to.
            task: The comprehensive conversation context summary
                and goal to be achieved regarding user inquiry.
            tool_context: The tool context this method runs in.

        Yields:
            A dictionary of JSON data.
        """
        if agent_name not in self.remote_agent_connections:
            raise ValueError(f'Agent {agent_name} not found')
        print(
            'sending message to',
            agent_name,
        )
        state = tool_context.state
        state['active_agent'] = agent_name
        client = self.remote_agent_connections[agent_name]

        if not client:
            raise ValueError(f'Client not available for {agent_name}')

        if 'context_id' in state:
            context_id = state['context_id']
        else:
            context_id = str(uuid.uuid4())

        message_id = ''
        metadata = {}
        if 'input_message_metadata' in state:
            metadata.update(**state['input_message_metadata'])
            if 'message_id' in state['input_message_metadata']:
                message_id = state['input_message_metadata']['message_id']
        if not message_id:
            message_id = str(uuid.uuid4())

        payload = {
            'message': {
                'role': 'user',
                'parts': [
                    {'type': 'text', 'text': task}
                ],  # Use the 'task' argument here
                'messageId': message_id,
            },
        }

        if context_id:
            payload['message']['contextId'] = context_id

        message_request = SendMessageRequest(
            id=message_id, params=MessageSendParams.model_validate(payload)
        )
        send_response: SendMessageResponse = await client.send_message(
            message_request=message_request
        )
        print(
            'send_response',
            send_response.model_dump_json(exclude_none=True, indent=2),
        )

        if not isinstance(send_response.root, SendMessageSuccessResponse):
            print('received non-success response. Aborting get task ')
            return None

        if not isinstance(send_response.root.result, Task):
            print('received non-task response. Aborting get task ')
            return None

        return send_response.root.result


def _get_initialized_coordinator_agent_sync() -> Agent:
    """Synchronously creates and initializes the CoordinatorAgent."""

    async def _async_main() -> Agent:
        coordinator_agent_instance = await CoordinatorAgent.create(
            remote_agent_addresses=[
                os.getenv('CONTENT_EDITOR_AGENT_URL', 'http://content-editor-agent:8080'),
                os.getenv('CONTENT_WRITER_AGENT_URL', 'http://content-writer-agent:8080'),
                os.getenv('CONTENT_PLANNER_AGENT_URL', 'http://content-planner-agent:8080'),
            ]
        )
        return coordinator_agent_instance.create_agent()

    try:
        return asyncio.run(_async_main())
    except RuntimeError as e:
        if 'asyncio.run() cannot be called from a running event loop' in str(e):
            print(
                f'Warning: Could not initialize CoordinatorAgent with asyncio.run(): {e}. '
                'This can happen if an event loop is already running (e.g., in Jupyter). '
                'Consider initializing CoordinatorAgent within an async function in your application.'
            )
        raise


root_agent = _get_initialized_coordinator_agent_sync()
