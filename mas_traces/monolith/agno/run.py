import agentops
from agno.agent import Agent
from agno.models.google import Gemini

agentops.init()

agent = Agent(
        model=Gemini(id="gemini-2.0-flash"),
        markdown=True,
    )

agent.print_response("Share a 2 sentence horror story.")