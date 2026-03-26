"""Tooling for the marketing_agency mas_creator demo."""

from __future__ import annotations

import inspect
from typing import Any

try:
    from google.adk.tools import ToolContext
    from google.adk.tools import google_search as _adk_google_search
    from google.adk.tools import load_artifacts as _adk_load_artifacts
except Exception:
    ToolContext = Any  # type: ignore[assignment]
    _adk_google_search = None
    _adk_load_artifacts = None

try:
    from google.genai import Client, types
except Exception:
    Client = None
    types = None


MODEL_IMAGE = "imagen-3.0-generate-002"


def google_search(query: str) -> Any:
    """Searches Google for the given query text."""
    if _adk_google_search is None:
        return "google_search tool is unavailable in this environment."

    try:
        return _adk_google_search(query)
    except TypeError:
        return _adk_google_search(query=query)


async def generate_image(img_prompt: str, tool_context: "ToolContext" = None) -> dict[str, Any]:
    """Generates an image based on the prompt."""
    if Client is None:
        return {"status": "failed", "detail": "google-genai is unavailable."}

    client = Client()
    response = client.models.generate_images(
        model=MODEL_IMAGE,
        prompt=img_prompt,
        config={"number_of_images": 1},
    )
    if not response.generated_images:
        return {"status": "failed"}

    image_bytes = response.generated_images[0].image.image_bytes

    # Save locally for user visibility.
    with open("generated_logo.png", "wb") as file_handle:
        file_handle.write(image_bytes)

    if tool_context is not None and types is not None:
        await tool_context.save_artifact(
            "image.png",
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        )

    return {
        "status": "success",
        "detail": "Image generated successfully and stored in artifacts.",
        "filename": "image.png",
    }


async def load_artifacts(tool_context: "ToolContext" = None) -> Any:
    """Loads previously saved artifacts from the ADK tool context."""
    if _adk_load_artifacts is None:
        return {"status": "failed", "detail": "load_artifacts tool is unavailable."}
    if tool_context is None:
        return {
            "status": "failed",
            "detail": "tool_context is required for load_artifacts.",
        }

    result = _adk_load_artifacts(tool_context)
    if inspect.isawaitable(result):
        return await result
    return result
