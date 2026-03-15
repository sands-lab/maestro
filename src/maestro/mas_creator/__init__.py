"""mas_creator — build any-agent Agents from structured JSON input.

Public API::

    from maestro.mas_creator import AgentBuilder, AgentInput
    from maestro.mas_creator import RoundRobinGroup, StarGroup, HandoffGroup

    # Build agents
    agent = AgentBuilder.build(AgentInput(
        framework="openai",
        model_id="openai:gpt-4o",
        instructions="You are a helpful assistant.",
    ))

    # Round-Robin
    group = RoundRobinGroup(agents=[agent_a, agent_b], termination_keyword="TERMINATE")
    result = await group.run("Some task")

    # Star/Tree topology
    star = StarGroup(orchestrator=orchestrator, sub_agents=[agent_a, agent_b])
    result = await star.run("Some task")

    # Handoff
    handoff = HandoffGroup(
        agents={"triage": triage_agent, "expert": expert_agent},
        entry_agent_name="triage",
        termination_keyword="TERMINATE",
    )
    result = await handoff.run("Some task")
"""

from .builder import AgentBuilder, GroupBuilder
from .controllers import HandoffGroup, RoundRobinGroup, StarGroup
from .models import AgentInput, GroupInput

__all__ = [
    "AgentBuilder",
    "AgentInput",
    "GroupBuilder",
    "GroupInput",
    "RoundRobinGroup",
    "StarGroup",
    "HandoffGroup",
]
