from google.adk.agents import Agent
from google.adk.tools import google_search
import os
from google.adk.models.lite_llm import LiteLlm

if os.getenv("PROVIDER") == "google":
    llm = "gemini-2.5-flash"
elif os.getenv("PROVIDER") == "aliyun" or os.getenv("PROVIDER") == "ollama":
    if os.getenv("PROVIDER") == "aliyun":
        api_key = os.getenv("ALIYUN_API_KEY")
        api_base = os.getenv("API_BASE")
    else:
        api_key = os.getenv("OLLAMA_API_KEY")
        api_base = os.getenv("OLLAMA_API_BASE")

    llm = LiteLlm(
        model=os.getenv("MODEL", "gemini-2.5-flash"),
        api_base=api_base,
        api_key=api_key
    )
else:
    raise ValueError("Unsupported PROVIDER. Please set PROVIDER to 'google', 'aliyun', or 'ollama'.")

def _use_mock_llm() -> bool:
    return os.getenv("USE_MOCK_LLM", "").lower() in ("1", "true", "yes", "y")

root_agent = Agent(
    name="content_planner_agent",
    model=llm,
    description=("Planning agent that creates a detailed and logical outline for a piece of content,"
                 "given a high-level description."),
    instruction=("You are an expert content planner. Your task is to create a detailed and logical outline for a piece"
                 "of content, given a high-level description."),
    tools=[] if _use_mock_llm() else [google_search],
)
