"""Multi-agent communication controllers for mas_creator.

Three coordination patterns are provided:

* :class:`RoundRobinGroup`  — Round-robin, cyclically schedules all agents until a termination keyword appears.
* :class:`StarGroup`        — Star/Tree topology, wraps sub-agents as callable tools and attaches them to a
                              central orchestrator agent, to be called autonomously by the LLM.
* :class:`HandoffGroup`     — Handoff, if the current agent outputs ``HANDOFF:<name>``, control is transferred
                              to the corresponding agent until a termination keyword appears.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from any_agent import AgentConfig, AnyAgent

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# RoundRobinGroup
# ---------------------------------------------------------------------------

class RoundRobinGroup:
    """Calls a group of :class:`~any_agent.AnyAgent` sequentially in round-robin order, sharing conversational context.

    Each round passes the full conversation history (JSON string) to the current agent, and appends its reply to the shared
    context. When the last 20 characters of any agent's output contain the ``termination_keyword``,
    the loop stops and returns the final output.

    Args:
        agents: List of agents participating in the round-robin, in the order they will be called.
        termination_keyword: Keyword that triggers termination when appearing at the end of the output, e.g., ``"TERMINATE"``.

    Example::

        group = RoundRobinGroup(
            agents=[agent_a, agent_b, agent_c],
            termination_keyword="TERMINATE",
        )
        result = await group.run("Plan a 3-day trip to Nepal.")
        print(result)
    """

    def __init__(self, agents: list[AnyAgent], termination_keyword: str) -> None:
        self.agents = agents
        self.termination_keyword = termination_keyword
        self._index: int = 0
        self._context: list[dict[str, str]] = []

    def _next_agent(self) -> AnyAgent:
        """Return the next agent in round-robin order."""
        agent = self.agents[self._index]
        self._index = (self._index + 1) % len(self.agents)
        return agent

    def reset(self) -> None:
        """Reset the group state so it can be reused for a new task."""
        self._index = 0
        self._context = []

    async def run(self, task: str) -> str:
        """Run the round-robin loop until the termination keyword is detected.

        Args:
            task: The initial user task / prompt.

        Returns:
            The final agent output that triggered termination.
        """
        self._context.append({"role": "user", "content": task})

        output = ""
        while True:
            agent = self._next_agent()
            agent_trace = await agent.run_async(json.dumps(self._context))
            output = str(agent_trace.final_output)
            self._context.append({"role": "assistant", "content": output})

            # Check termination in the last 20 characters
            if self.termination_keyword in output[-20:]:
                break

        return output


# ---------------------------------------------------------------------------
# StarGroup
# ---------------------------------------------------------------------------

class StarGroup:
    """Star/Tree topology: Wraps sub-agents as callable tools attached to a central orchestrator.

    Each sub :class:`~any_agent.AnyAgent` is wrapped as an ``async`` callable object, whose
    ``__doc__`` comes from ``agent.config.description`` (or ``agent.config.name``).
    These callables are passed as a list of tools to the orchestrator, and the orchestrator's LLM decides
    autonomously when and which sub-agent to call.

    Args:
        orchestrator: Central coordinating agent (can be created via :meth:`~mas_creator.AgentBuilder.build`),
            whose ``tools`` list will automatically append the callables of all sub-agents during construction.
        sub_agents: List of sub-agents to be mounted as tools.

    Example::

        orchestrator = AgentBuilder.build(AgentInput(
            framework="openai",
            model_id="openai:gpt-4o",
            instructions="Use the available agents to answer the query.",
        ))
        star = StarGroup(orchestrator=orchestrator, sub_agents=[agent_a, agent_b])
        result = await star.run("What is the weather in Tokyo?")
        print(result)
    """

    def __init__(self, orchestrator: AnyAgent, sub_agents: list[AnyAgent]) -> None:
        self.orchestrator = orchestrator
        self.sub_agents = sub_agents
        self._tool_callables = self._wrap_sub_agents()
        # Attach wrapped callables to the orchestrator's tool list at runtime
        self.orchestrator.config.tools.extend(self._tool_callables)

    def _wrap_sub_agents(self) -> list:
        """Return a list of async callables, one per sub-agent."""
        callables = []
        for agent in self.sub_agents:
            # Capture agent in closure
            _agent = agent
            _doc = _agent.config.description or _agent.config.name

            async def _call(query: str, _a: AnyAgent = _agent) -> str:
                trace = await _a.run_async(query)
                return str(trace.final_output)

            _call.__name__ = _agent.config.name
            _call.__doc__ = _doc
            callables.append(_call)
        return callables

    async def run(self, task: str) -> str:
        """Dispatch the task to the orchestrator, which calls sub-agents as tools.

        Args:
            task: The user task / prompt sent to the orchestrator.

        Returns:
            Final output produced by the orchestrator.
        """
        trace = await self.orchestrator.run_async(task)
        return str(trace.final_output)


# ---------------------------------------------------------------------------
# HandoffGroup
# ---------------------------------------------------------------------------

class HandoffGroup:
    """Handoff topology: If the current agent's output contains ``HANDOFF:<name>``, control
    is transferred to the agent with the corresponding name, and execution continues until a termination keyword appears.

    On each handoff, the complete conversation context (JSON string) is passed to the next agent, ensuring
    it is aware of the existing conversation history.

    Args:
        agents: Dictionary of agents participating in the handoff, with keys as actor names and values as
            :class:`~any_agent.AnyAgent` instances.
        entry_agent_name: The name of the initially called agent. Must exist in ``agents``.
        termination_keyword: Keyword that triggers termination when it appears in the output, e.g., ``"TERMINATE"``.
        handoff_prefix: The prefix used to specify the next agent, defaulting to ``"HANDOFF:"``.
            The format is ``HANDOFF:<agent_name>``, and it can appear on any line of the output.
        max_turns: Maximum number of turns to prevent infinite loops. Defaults to ``50``. Exceeding this throws a
            :class:`RuntimeError`.
        verbose: If ``True``, prints the current agent's name and an output summary in each turn.

    Example::

        group = HandoffGroup(
            agents={"triage": triage_agent, "expert": expert_agent},
            entry_agent_name="triage",
            termination_keyword="TERMINATE",
            verbose=True,
        )
        result = await group.run("I need help with my order.")
        print(result)
    """

    DEFAULT_HANDOFF_PREFIX = "HANDOFF:"
    DEFAULT_MAX_TURNS = 50

    def __init__(
        self,
        agents: dict[str, AnyAgent],
        entry_agent_name: str,
        termination_keyword: str,
        handoff_prefix: str = DEFAULT_HANDOFF_PREFIX,
        max_turns: int = DEFAULT_MAX_TURNS,
        verbose: bool = False,
    ) -> None:
        if entry_agent_name not in agents:
            raise ValueError(
                f"entry_agent_name '{entry_agent_name}' not found in agents dict. "
                f"Available names: {list(agents.keys())}"
            )
        self.agents = agents
        self.entry_agent_name = entry_agent_name
        self.termination_keyword = termination_keyword
        self.handoff_prefix = handoff_prefix
        self.max_turns = max_turns
        self.verbose = verbose
        self._context: list[dict[str, str]] = []

    def reset(self) -> None:
        """Reset conversation context so the group can handle a new task."""
        self._context = []

    def _format_context(self) -> str:
        """Render the conversation context as human-readable text.

        Each entry is formatted as::

            [role]: content

        This is much easier for LLMs to parse than a raw JSON dump,
        enabling agents to reliably track what has already been done.
        """
        lines: list[str] = []
        for msg in self._context:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"[{role}]: {content}")
        return "\n\n".join(lines)

    def _parse_handoff(self, output: str) -> tuple[str | None, str]:
        """Scan all lines of ``output`` for a handoff directive.

        The directive may appear on any line, allowing agents to write
        explanatory prose before or after the ``HANDOFF:<name>`` token.

        Returns a tuple of ``(next_agent_name | None, message_body)``.
        ``message_body`` is the full output with the directive line removed.
        If no directive is found, ``next_agent_name`` is ``None``.
        """
        lines = output.splitlines()
        for i, line in enumerate(lines):
            stripped_line = line.strip()
            if stripped_line.startswith(self.handoff_prefix):
                rest = stripped_line[len(self.handoff_prefix):]
                parts = rest.split(None, 1)
                next_name = parts[0].rstrip(",;.") if parts else ""
                # Body = all lines except the directive line
                body_lines = lines[:i] + lines[i + 1:]
                body = "\n".join(body_lines).strip()
                return next_name, body
        return None, output

    async def run(self, task: str) -> str:
        """Run the handoff loop starting from ``entry_agent_name``.

        Args:
            task: The initial user task / prompt.

        Returns:
            The final agent output that triggered termination.

        Raises:
            RuntimeError: If the number of turns exceeds ``max_turns``.
            ValueError: If an agent requests a handoff to an unknown agent name.
        """
        self._context.append({"role": "user", "content": task})

        current_name = self.entry_agent_name
        output = ""
        turn = 0

        while True:
            if turn >= self.max_turns:
                raise RuntimeError(
                    f"HandoffGroup exceeded max_turns={self.max_turns}. "
                    "Ensure agents eventually emit the termination keyword."
                )
            turn += 1

            agent = self.agents[current_name]
            if self.verbose:
                print(f"[Turn {turn}] Agent: {current_name}")

            agent_trace = await agent.run_async(self._format_context())
            output = str(agent_trace.final_output)

            if self.verbose:
                preview = output[:120].replace("\n", " ")
                print(f"         Output: {preview}{'…' if len(output) > 120 else ''}")

            # Record this turn in the shared context.
            # Strip the HANDOFF directive line so downstream agents (especially the
            # planner) do not get confused by seeing "HANDOFF:xxx" in past messages.
            _, body = self._parse_handoff(output)
            context_content = body if body.strip() else output
            self._context.append({
                "role": "assistant",
                "content": f"[{current_name}]: {context_content}",
            })

            # Termination check (before routing so TERMINATE wins over HANDOFF)
            if self.termination_keyword in output:
                if self.verbose:
                    print(f"[Turn {turn}] Termination keyword detected. Stopping.")
                break

            # Parse handoff directive from any line in the output
            next_name, _body = self._parse_handoff(output)

            if next_name is not None:
                if next_name not in self.agents:
                    raise ValueError(
                        f"Agent '{current_name}' requested handoff to unknown agent "
                        f"'{next_name}'. Available: {list(self.agents.keys())}"
                    )
                if self.verbose:
                    print(f"         Handoff → {next_name}")
                current_name = next_name
            # If no handoff and no termination, the same agent runs again.
            # Agents should be prompted to always emit a directive.

        return output
