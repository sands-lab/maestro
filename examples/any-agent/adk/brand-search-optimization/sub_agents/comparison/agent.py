from any_agent import AnyAgent, AgentConfig
from sub_agents.comparison import prompt

async def comparison_generator_agent_func(query: str) -> str:
    comparison_generator_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id = "openai:gpt-5-mini",
            instructions = prompt.COMPARISON_AGENT_PROMPT,
        ),
    )
    agent_trace = await comparison_generator_agent.run_async(query)
    return str(agent_trace.final_output)

comparison_generator_agent_func.__doc__ = prompt.COMPARISON_AGENT_PROMPT

async def comparison_critic_agent_func(query: str) -> str:
    comparison_critic_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id = "openai:gpt-5-mini",
            instructions = prompt.COMPARISON_CRITIC_AGENT_PROMPT,
        ),
    )
    agent_trace = await comparison_critic_agent.run_async(query)
    return str(agent_trace.final_output)

comparison_critic_agent_func.__doc__ = prompt.COMPARISON_CRITIC_AGENT_PROMPT

async def comparison_root_agent_func(query: str) -> str:
    comparison_root_agent = await AnyAgent.create_async(
        agent_framework="google",
        agent_config=AgentConfig(
            model_id = "openai:gpt-5-mini",
            instructions = prompt.COMPARISON_ROOT_AGENT_PROMPT,
            tools = [
                comparison_generator_agent_func,
                comparison_critic_agent_func,
            ]
        ),
    )
    agent_trace = await comparison_root_agent.run_async(query)
    return str(agent_trace.final_output)

comparison_root_agent_func.__doc__ = prompt.COMPARISON_ROOT_AGENT_PROMPT
