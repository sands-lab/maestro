
"""Basic evalualtion for Image Scoring."""

import pathlib
import dotenv
import pytest
from google.adk.evaluation import AgentEvaluator

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session", autouse=True)
def load_env():
    dotenv.load_dotenv()


@pytest.mark.asyncio
async def test_all():
    """Test the agent's basic ability on a few examples."""
    await AgentEvaluator.evaluate(
        "image_scoring",
        str(pathlib.Path(__file__).parent / "data"),
        num_runs=2,
    )
