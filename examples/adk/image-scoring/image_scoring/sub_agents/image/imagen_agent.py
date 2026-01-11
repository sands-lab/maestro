from .prompt import IMAGEGEN_PROMPT
from google.adk.agents import Agent
from .tools.image_generation_tool import generate_images
from ... import config

image_generation_agent = Agent(
    name="image_generation_agent",
    model=config.GENAI_MODEL,
    description=("You are an expert in creating images with imagen 3"),
    instruction=(IMAGEGEN_PROMPT),
    tools=[generate_images],
    output_key="output_image",
)
