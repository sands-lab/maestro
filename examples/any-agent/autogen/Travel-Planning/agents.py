from any_agent import AnyAgent, AgentConfig
from prompts import PLANNER_PROMPT, LOCAL_PROMPT, LANGUAGE_PROMPT, SUMMARY_PROMPT, PLANNER_DESCRIPTION, LOCAL_DESCRIPTION, LANGUAGE_DESCRIPTION, SUMMARY_DESCRIPTION

async def create_planner_agent():
    planner_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id="openai:gpt-5-mini",
            instructions=PLANNER_PROMPT,
            description=PLANNER_DESCRIPTION
        ),
    )
    return planner_agent

async def callable_planner_agent(task: str):
    planner_agent = await create_planner_agent()
    agent_trace = await planner_agent.run_async(task)
    return str(agent_trace.final_output)

callable_planner_agent.__doc__ = PLANNER_DESCRIPTION

async def create_local_agent():
    local_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id="openai:gpt-5-mini",
            instructions=LOCAL_PROMPT,
            description=LOCAL_DESCRIPTION
        ),
    )
    return local_agent

async def callable_local_agent(task: str):
    local_agent = await create_local_agent()
    agent_trace = await local_agent.run_async(task)
    return str(agent_trace.final_output)

callable_local_agent.__doc__ = LOCAL_DESCRIPTION

async def create_language_agent():
    language_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id="openai:gpt-5-mini",
            instructions=LANGUAGE_PROMPT,
            description=LANGUAGE_DESCRIPTION
        ),
    )
    return language_agent

async def callable_language_agent(task: str):
    language_agent = await create_language_agent()
    agent_trace = await language_agent.run_async(task)
    return str(agent_trace.final_output)

callable_language_agent.__doc__ = LANGUAGE_DESCRIPTION

async def create_summary_agent():
    summary_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id="openai:gpt-5-mini",
            instructions=SUMMARY_PROMPT,
            description=SUMMARY_DESCRIPTION
        ),
    )
    return summary_agent

async def callable_summary_agent(task: str):
    summary_agent = await create_summary_agent()
    agent_trace = await summary_agent.run_async(task)
    return str(agent_trace.final_output)

callable_summary_agent.__doc__ = SUMMARY_DESCRIPTION