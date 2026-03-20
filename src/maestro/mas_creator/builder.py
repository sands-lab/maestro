"""AgentBuilder: construct and return an AnyAgent from a JSON-compatible AgentInput."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any

from any_agent import AgentConfig, AgentFramework, AnyAgent
from any_agent.tools import send_console_message

from .controllers import HandoffGroup, RoundRobinGroup, StarGroup
from .models import AgentInput, GroupInput


class AgentBuilder:
    """Build an :class:`any_agent.AnyAgent` from a structured :class:`AgentInput`.

    Usage::

        from maestro.mas_creator import AgentBuilder, AgentInput
        from my_tools import search_web, fetch_page

        agent_input = AgentInput(
            framework="openai",
            model_id="openai:gpt-4o",
            tools=[search_web, fetch_page],
            instructions="You are a helpful research assistant.",
        )

        agent = AgentBuilder.build(agent_input)
        trace = agent.run("What is the capital of France?")
        print(trace.final_output)

    Alternatively, you can build directly from a plain :class:`dict` (e.g. parsed
    JSON) using :meth:`build_from_dict`::

        agent = AgentBuilder.build_from_dict(
            {
                "framework": "tinyagent",
                "model_id": "mistral:mistral-small-latest",
                "tools": [search_web],
                "instructions": "Answer briefly.",
            }
        )
    """

    @staticmethod
    def human_input(query: str) -> str:
        """Ask the user a question in console and return the response."""
        return send_console_message(user="User", query=query)

    @staticmethod
    def _build_tools(agent_input: AgentInput) -> list[Any]:
        """Build the final tool list for an agent, including optional human-input tooling."""
        tools = list(agent_input.tools)
        if agent_input.human_input and AgentBuilder.human_input not in tools:
            tools.append(AgentBuilder.human_input)
        return tools

    @staticmethod
    def _make_agent_config(agent_input: AgentInput) -> AgentConfig:
        """Translate an :class:`AgentInput` into an :class:`any_agent.AgentConfig`."""
        return AgentConfig(
            model_id=agent_input.model_id,
            tools=AgentBuilder._build_tools(agent_input),
            name=agent_input.name,
            instructions=agent_input.instructions,
            description=agent_input.description,
            api_base=agent_input.api_base,
            api_key=agent_input.api_key,
            model_args=agent_input.model_args,
            agent_args=agent_input.agent_args,
        )

    @classmethod
    def build(cls, agent_input: AgentInput) -> AnyAgent:
        """Build and return a synchronous :class:`~any_agent.AnyAgent`.

        Args:
            agent_input: Validated input describing the agent to create.

        Returns:
            A fully initialised :class:`~any_agent.AnyAgent` instance ready
            for calls to :meth:`~any_agent.AnyAgent.run`.

        Raises:
            ValueError: If the ``framework`` field is not a supported value.
        """
        framework = AgentFramework.from_string(agent_input.framework)
        agent_config = cls._make_agent_config(agent_input)
        return AnyAgent.create(framework, agent_config)

    @classmethod
    async def build_async(cls, agent_input: AgentInput) -> AnyAgent:
        """Build and return an :class:`~any_agent.AnyAgent` asynchronously.

        Prefer this method inside ``async`` contexts (e.g. FastAPI handlers,
        Jupyter notebooks) to avoid blocking the event loop.

        Args:
            agent_input: Validated input describing the agent to create.

        Returns:
            A fully initialised :class:`~any_agent.AnyAgent` instance ready
            for calls to :meth:`~any_agent.AnyAgent.run_async`.

        Raises:
            ValueError: If the ``framework`` field is not a supported value.
        """
        framework = AgentFramework.from_string(agent_input.framework)
        agent_config = cls._make_agent_config(agent_input)
        return await AnyAgent.create_async(framework, agent_config)

    @classmethod
    def build_from_dict(
        cls,
        data: dict[str, Any],
    ) -> AnyAgent:
        """Build an agent directly from a raw dictionary (e.g. parsed JSON).

        The dictionary is validated against :class:`AgentInput` before the
        agent is created, so any schema violations raise a
        :class:`pydantic.ValidationError`.

        Args:
            data: Dictionary containing at minimum ``framework`` and
                ``model_id`` keys. The ``tools`` value, if provided, must be
                a list of Python callables (not string names).

        Returns:
            A fully initialised :class:`~any_agent.AnyAgent`.
        """
        agent_input = AgentInput.model_validate(data)
        return cls.build(agent_input)

    @classmethod
    async def build_from_dict_async(
        cls,
        data: dict[str, Any],
    ) -> AnyAgent:
        """Async variant of :meth:`build_from_dict`.

        Args:
            data: Dictionary containing at minimum ``framework`` and
                ``model_id`` keys.

        Returns:
            A fully initialised :class:`~any_agent.AnyAgent`.
        """
        agent_input = AgentInput.model_validate(data)
        return await cls.build_async(agent_input)


class GroupBuilder:
    """Build multi-agent groupings from a structured :class:`GroupInput` or JSON.

    Usage::

        from maestro.mas_creator import GroupBuilder
        from my_tools import TOOL_REGISTRY

        group = GroupBuilder.build_from_file("agents_config.json", tool_registry=TOOL_REGISTRY)
        result = await group.run("What is the capital of France?")
    """

    @classmethod
    def load_tools_from_file(cls, tools_path: str | Path) -> dict[str, Any]:
        """Dynamically load and extract all functions from a Python file.

        Useful for bypassing a manual tool registry.

        Args:
            tools_path: Path to the Python file containing tool definitions.

        Returns:
            A dictionary mapping function names to callable objects.
        """
        tools_path = Path(tools_path).resolve()
        if not tools_path.exists():
            raise FileNotFoundError(f"Tools file not found: {tools_path}")

        module_name = tools_path.stem
        spec = importlib.util.spec_from_file_location(module_name, tools_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load tools from {tools_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        tool_registry: dict[str, Any] = {}
        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and not name.startswith("_"):
                tool_registry[name] = obj

        return tool_registry

    @classmethod
    def _resolve_tools(cls, data: dict[str, Any], tool_registry: dict[str, Any]) -> None:
        """In-place resolution of string tool names to callables.

        Searches recursively for "tools" arrays and maps strings to callables
        from `tool_registry`.
        """
        if isinstance(data, dict):
            for k, v in data.items():
                if k == "tools" and isinstance(v, list):
                    resolved = []
                    for tool in v:
                        if isinstance(tool, str):
                            if tool not in tool_registry:
                                raise KeyError(f"Tool '{tool}' not found in tool_registry.")
                            resolved.append(tool_registry[tool])
                        else:
                            resolved.append(tool)
                    data[k] = resolved
                else:
                    cls._resolve_tools(v, tool_registry)
        elif isinstance(data, list):
            for item in data:
                cls._resolve_tools(item, tool_registry)

    @classmethod
    def build(cls, group_input: GroupInput) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Build a group controller directly from a :class:`GroupInput` schema.

        Args:
            group_input: Validated input describing the group to create.

        Returns:
            A fully initialized group controller.

        Raises:
            ValueError: If the group configuration is missing required fields.
        """
        if group_input.group_type == "round_robin":
            if not group_input.agents:
                raise ValueError("'round_robin' group requires an 'agents' list.")
            agents = [AgentBuilder.build(ainput) for ainput in group_input.agents]
            return RoundRobinGroup(
                agents=agents,
                termination_keyword=group_input.termination_keyword or "TERMINATE",
            )
        elif group_input.group_type == "star":
            if not group_input.orchestrator or not group_input.sub_agents:
                raise ValueError("'star' group requires both 'orchestrator' and 'sub_agents'.")
            orchestrator = AgentBuilder.build(group_input.orchestrator)
            sub_agents = [AgentBuilder.build(ainput) for ainput in group_input.sub_agents]
            return StarGroup(
                orchestrator=orchestrator,
                sub_agents=sub_agents,
            )
        elif group_input.group_type == "handoff":
            if not group_input.agents or not group_input.entry_agent_name:
                raise ValueError("'handoff' group requires 'agents' list and 'entry_agent_name'.")
            agents = {ainput.name: AgentBuilder.build(ainput) for ainput in group_input.agents}
            kwargs = {}
            if group_input.handoff_prefix:
                kwargs["handoff_prefix"] = group_input.handoff_prefix
            if group_input.max_turns:
                kwargs["max_turns"] = group_input.max_turns
            return HandoffGroup(
                agents=agents,
                entry_agent_name=group_input.entry_agent_name,
                termination_keyword=group_input.termination_keyword or "TERMINATE",
                verbose=group_input.verbose,
                **kwargs,
            )
        else:
            raise ValueError(f"Unknown group type: {group_input.group_type}")

    @classmethod
    async def build_async(cls, group_input: GroupInput) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Build a group controller asynchronously from a :class:`GroupInput` schema."""
        if group_input.group_type == "round_robin":
            if not group_input.agents:
                raise ValueError("'round_robin' group requires an 'agents' list.")
            agents = [await AgentBuilder.build_async(ainput) for ainput in group_input.agents]
            return RoundRobinGroup(
                agents=agents,
                termination_keyword=group_input.termination_keyword or "TERMINATE",
            )
        elif group_input.group_type == "star":
            if not group_input.orchestrator or not group_input.sub_agents:
                raise ValueError("'star' group requires both 'orchestrator' and 'sub_agents'.")
            orchestrator = await AgentBuilder.build_async(group_input.orchestrator)
            sub_agents = [await AgentBuilder.build_async(ainput) for ainput in group_input.sub_agents]
            return StarGroup(
                orchestrator=orchestrator,
                sub_agents=sub_agents,
            )
        elif group_input.group_type == "handoff":
            if not group_input.agents or not group_input.entry_agent_name:
                raise ValueError("'handoff' group requires 'agents' list and 'entry_agent_name'.")
            agents = {ainput.name: await AgentBuilder.build_async(ainput) for ainput in group_input.agents}
            kwargs = {}
            if group_input.handoff_prefix:
                kwargs["handoff_prefix"] = group_input.handoff_prefix
            if group_input.max_turns:
                kwargs["max_turns"] = group_input.max_turns
            return HandoffGroup(
                agents=agents,
                entry_agent_name=group_input.entry_agent_name,
                termination_keyword=group_input.termination_keyword or "TERMINATE",
                verbose=group_input.verbose,
                **kwargs,
            )
        else:
            raise ValueError(f"Unknown group type: {group_input.group_type}")

    @classmethod
    def _create_agent_tool_wrapper(cls, agent_data: dict[str, Any], is_async: bool = False):
        """Creates a callable tool wrapper for an agent, enabling agent-as-a-tool."""
        name = agent_data.get("name", "agent_func")
        desc = agent_data.get("description", agent_data.get("instructions", "Agent tool wrapper"))

        if is_async:
            async def agent_tool_func(query: str) -> str:
                from .models import AgentInput
                from .builder import AgentBuilder
                agent_input = AgentInput(**agent_data)
                agent = await AgentBuilder.build_async(agent_input)
                agent_trace = await agent.run_async(query)
                return str(agent_trace.final_output)

            agent_tool_func.__name__ = name
            agent_tool_func.__doc__ = desc
            return agent_tool_func
        else:
            def agent_tool_func(query: str) -> str:
                from .models import AgentInput
                from .builder import AgentBuilder
                agent_input = AgentInput(**agent_data)
                agent = AgentBuilder.build(agent_input)
                agent_trace = agent.run(query)
                return str(agent_trace.final_output)

            agent_tool_func.__name__ = name
            agent_tool_func.__doc__ = desc
            return agent_tool_func

    @classmethod
    def _register_agent_wrappers(cls, data: dict[str, Any], tool_registry: dict[str, Any], is_async: bool):
        agents = []
        if "agents" in data and isinstance(data["agents"], list):
            agents.extend(data["agents"])
        if "sub_agents" in data and isinstance(data["sub_agents"], list):
            agents.extend(data["sub_agents"])
        if "orchestrator" in data and isinstance(data["orchestrator"], dict):
            agents.append(data["orchestrator"])
        
        for agent_data in agents:
            agent_name = agent_data.get("name")
            if agent_name and agent_name not in tool_registry:
                tool_registry[agent_name] = cls._create_agent_tool_wrapper(agent_data, is_async=is_async)

    @classmethod
    def build_from_dict(
        cls,
        data: dict[str, Any],
        tool_registry: dict[str, Any] | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Build a group directly from a raw dictionary (e.g. parsed JSON).

        Args:
            data: Group configuration dictionary.
            tool_registry: Dictionary mapping string tool names to callables.

        Returns:
            A fully initialized round robin, star, or handoff group.
        """
        tool_registry = dict(tool_registry) if tool_registry else {}
        cls._register_agent_wrappers(data, tool_registry, is_async=False)
        cls._resolve_tools(data, tool_registry)
        group_input = GroupInput.model_validate(data)
        return cls.build(group_input)

    @classmethod
    async def build_from_dict_async(
        cls,
        data: dict[str, Any],
        tool_registry: dict[str, Any] | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Async variant of :meth:`build_from_dict`."""
        tool_registry = dict(tool_registry) if tool_registry else {}
        cls._register_agent_wrappers(data, tool_registry, is_async=True)
        cls._resolve_tools(data, tool_registry)
        group_input = GroupInput.model_validate(data)
        return await cls.build_async(group_input)

    @classmethod
    def build_from_file(
        cls,
        path: str | Path,
        tool_registry: dict[str, Any] | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Build a group directly from a JSON file on disk.

        Args:
            path: Path to the JSON configuration file.
            tool_registry: Dictionary mapping string tool names to callables.

        Returns:
            A fully initialized round robin, star, or handoff group.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.build_from_dict(data, tool_registry)

    @classmethod
    async def build_from_file_async(
        cls,
        path: str | Path,
        tool_registry: dict[str, Any] | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Async variant of :meth:`build_from_file`."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return await cls.build_from_dict_async(data, tool_registry)

    @classmethod
    def build_from_config(
        cls,
        config_path: str | Path,
        tools_path: str | Path | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Convenience method to construct a group from a JSON config and optional tools file.

        Args:
            config_path: Path to the JSON configuration file.
            tools_path: Optional path to a Python script containing tool callables.
                If provided, all public functions in the file are extracted into a registry.

        Returns:
            A fully initialized group structure.
        """
        registry = None
        if tools_path:
            registry = cls.load_tools_from_file(tools_path)
        return cls.build_from_file(config_path, tool_registry=registry)

    @classmethod
    async def build_from_config_async(
        cls,
        config_path: str | Path,
        tools_path: str | Path | None = None,
    ) -> RoundRobinGroup | StarGroup | HandoffGroup:
        """Async variant of :meth:`build_from_config`."""
        registry = None
        if tools_path:
            registry = cls.load_tools_from_file(tools_path)
        return await cls.build_from_file_async(config_path, tool_registry=registry)
