"""Pydantic models for the mas_creator agent builder input schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentInput(BaseModel):
    """JSON-serializable input schema for building an any-agent Agent.

    Attributes:
        framework: The agent framework to use. Must be one of the values
            supported by :class:`any_agent.AgentFramework`:
            ``"openai"``, ``"google"``, ``"langchain"``, ``"llama_index"``,
            ``"agno"``, ``"smolagents"``, ``"tinyagent"``.
        model_id: The model identifier passed to the underlying LLM provider.
            Follows `any-llm` provider syntax, e.g. ``"openai:gpt-4o"`` or
            ``"mistral:mistral-small-latest"``.
        tools: List of Python callables to expose as tools to the agent.
            Each element must be a regular Python function (decorated or not).
            MCP-based tools are not covered by this input model.
        name: Optional display name for the agent. Defaults to ``"any_agent"``.
        instructions: Optional system-level instructions (system prompt) for
            the agent.
        description: Optional human-readable description of the agent.
        api_base: Optional custom API endpoint URL for the model provider.
        api_key: Optional API key. Defaults to reading from environment
            variables (e.g. ``OPENAI_API_KEY``).
        model_args: Optional extra keyword arguments forwarded to the LLM at
            completion time (e.g. ``{"temperature": 0.7}``).
        agent_args: Optional extra keyword arguments forwarded to the
            underlying framework's agent constructor.
        human_input: If ``True``, auto-registers a single-argument
            ``human_input(query: str)`` tool for this agent.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    framework: str = Field(
        ...,
        description=(
            "Agent framework to use. Supported values: "
            "'openai', 'google', 'langchain', 'llama_index', "
            "'agno', 'smolagents', 'tinyagent'."
        ),
    )
    model_id: str = Field(
        ...,
        description=(
            "Model identifier for the underlying LLM, following any-llm "
            "provider syntax, e.g. 'openai:gpt-4o'."
        ),
    )
    tools: list[Callable[..., Any]] = Field(
        default_factory=list,
        description="List of Python callable tools available to the agent.",
    )
    name: str = Field(
        default="any_agent",
        description="Display name of the agent.",
    )
    instructions: str | None = Field(
        default=None,
        description="System prompt / instructions for the agent.",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of the agent.",
    )
    api_base: str | None = Field(
        default=None,
        description="Custom API base URL (e.g. for local Ollama servers).",
    )
    api_key: str | None = Field(
        default=None,
        description="Explicit API key; falls back to environment variables.",
    )
    model_args: dict[str, Any] | None = Field(
        default=None,
        description="Extra completion-time arguments forwarded to the LLM.",
    )
    agent_args: dict[str, Any] | None = Field(
        default=None,
        description="Extra arguments forwarded to the framework agent constructor.",
    )
    human_input: bool = Field(
        default=False,
        description=(
            "Whether to auto-register a single-argument human_input(query) "
            "callable tool for this agent."
        ),
    )


class GroupInput(BaseModel):
    """JSON-serializable input schema for building a multi-agent group.

    Attributes:
        group_type: The group topology to create ("round_robin", "star", or "handoff").
        agents: List of agent configurations (used for "round_robin" and "handoff").
        entry_agent_name: Initial agent to call (required for "handoff").
        termination_keyword: Keyword that triggers group termination.
        orchestrator: Central coordinating agent configuration (required for "star").
        sub_agents: List of sub-agent configurations mounted as tools (required for "star").
        handoff_prefix: Prefix to designate the next agent in a handoff loop.
        max_turns: Maximum interactions before aborting.
        verbose: Print debugging turns if True.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    group_type: Literal["round_robin", "star", "handoff"] = Field(
        ...,
        description='The topology pattern to orchestrate agents.',
    )
    agents: list[AgentInput] | None = Field(
        default=None,
        description="List of agent configs for round_robin or handoff topologies.",
    )
    entry_agent_name: str | None = Field(
        default=None,
        description="Name of the starting agent for handoff.",
    )
    termination_keyword: str | None = Field(
        default="TERMINATE",
        description="Keyword that halts execution (applicable for all group types).",
    )
    orchestrator: AgentInput | None = Field(
        default=None,
        description="Orchestrator agent config for star topology.",
    )
    sub_agents: list[AgentInput] | None = Field(
        default=None,
        description="List of sub-agent configs for star topology.",
    )
    handoff_prefix: str | None = Field(
        default=None,
        description="Prefix used to pass control in handoff.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Maximum turns before halting the group run.",
    )
    verbose: bool = Field(
        default=False,
        description="Whether to print turn-by-turn logs.",
    )
