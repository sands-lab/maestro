from google.adk.agents import Agent
from google.adk.tools import google_search


root_agent = Agent(
    name="content_writer_agent",
    model="gemini-2.5-flash",
    description=("Writing agent that creates detailed content based on a provided plan or simple story abstraction."),
    instruction=("You are an expert content writer. Your task is to write detailed and engaging content based on the"
                 "provided plan or simple story abstraction."),
    tools=[google_search],
)
