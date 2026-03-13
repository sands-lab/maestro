from agents import create_language_agent, create_local_agent, create_planner_agent, create_summary_agent
from RoundRobinGroup import RoundRobinGroup
import asyncio

async def amain():
    planner_agent = await create_planner_agent()
    local_agent = await create_local_agent()
    language_agent = await create_language_agent()
    summary_agent = await create_summary_agent()
    group = RoundRobinGroup(
        agents = [planner_agent, local_agent, language_agent, summary_agent],
        termination_condition="TERMINATE"
    )
    output = await group.run("Plan a 3 day trip to Nepal.")
    print(output)

if __name__ == "__main__":
    asyncio.run(amain())