from google.adk.agents import Agent
from google.adk.tools import google_search


root_agent = Agent(
    name="content_editor_agent",
    model="gemini-2.5-flash",
    description=("Editing agent that can proof-read and polish content"),
    instruction=("You are an expert editor that can proof-read and polish content."
                 "Your output should only consist of the final polished content."),
    tools=[google_search],
)