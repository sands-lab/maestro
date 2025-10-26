import asyncio
from autogen_core.models import UserMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelInfo
import agentops

agentops.init()

model_client = OpenAIChatCompletionClient(
    model="gemini-2.0-flash-lite",
    model_info=ModelInfo(vision=True, function_calling=True, json_output=True, family="unknown", structured_output=True)
    # api_key="KEY",
)

async def run_query(client):
    response = await model_client.create([UserMessage(content="What is the capital of France?", source="user")])
    print(response)
    await model_client.close()

if __name__ == "__main__":
    asyncio.run(run_query(model_client))