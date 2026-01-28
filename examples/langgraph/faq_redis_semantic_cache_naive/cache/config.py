"""Configuration helpers for API keys and environment validation."""

from __future__ import annotations

import os


def load_openai_key(env_var: str = "OPENAI_API_KEY") -> str:
    """Ensures the OpenAI API key exists and returns it."""
    api_key = os.getenv(env_var)
    if not api_key:
        raise RuntimeError(
            f"Set the {env_var} environment variable with an OpenAI API key "
            "before running the benchmark."
        )
    os.environ["OPENAI_API_KEY"] = api_key
    return api_key
