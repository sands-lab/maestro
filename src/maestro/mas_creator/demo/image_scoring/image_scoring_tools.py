"""Tools for the image-scoring mas_creator demo (no ADK dependencies).

Replaces the ADK ToolContext session state with a module-level _state dict.
All tools are plain synchronous Python functions auto-discovered by GroupBuilder.

Image generation: OpenAI DALL-E 3 (replaces Google Imagen 3.0)
Vision scoring:   OpenAI GPT-4o  (replaces Gemini multimodal)
"""
import os
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Module-level state — replaces ADK tool_context.state
# ---------------------------------------------------------------------------
_state: dict = {
    "loop_iteration": 0,
    "total_score": 0,
    "latest_image_path": None,
}

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
SCORE_THRESHOLD  = int(os.getenv("SCORE_THRESHOLD", "45"))
MAX_ITERATIONS   = int(os.getenv("MAX_ITERATIONS", "3"))
IMAGE_GEN_MODEL  = os.getenv("IMAGE_GEN_MODEL", "dall-e-3")   # OpenAI image generation
VISION_MODEL     = os.getenv("VISION_MODEL", "gpt-4o")         # OpenAI vision scoring

_POLICY_PATH = Path(__file__).parent / "policy.json"


def reset_state() -> None:
    """Reset module-level state before a new run (not exposed as agent tool)."""
    _state.update({"loop_iteration": 0, "total_score": 0, "latest_image_path": None})


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_policy() -> dict:
    """Return the image generation and compliance policy as text.

    Reads policy.json from the demo directory and returns its contents.
    """
    return {"policy_text": _POLICY_PATH.read_text(encoding="utf-8")}


def generate_image(imagen_prompt: str) -> dict:
    """Generate an image with DALL-E 3 and save it locally.

    Args:
        imagen_prompt: The positive prompt text for DALL-E 3.

    Returns:
        A dict with 'status', 'artifact_name', and 'local_path' on success,
        or 'status' and 'message' on error.
    """
    import urllib.request
    from openai import OpenAI

    client = OpenAI()  # reads OPENAI_API_KEY from environment
    try:
        response = client.images.generate(
            model=IMAGE_GEN_MODEL,
            prompt=imagen_prompt,
            size="1024x1792",   # closest DALL-E 3 size to 9:16 portrait
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
        iteration = _state.get("loop_iteration", 0)
        artifact_name = f"generated_image_{iteration}.png"
        local_path = Path(artifact_name).resolve()

        # Download image bytes from the returned URL
        urllib.request.urlretrieve(image_url, local_path)
        _state["latest_image_path"] = str(local_path)
        print(f"[generate_image] Image saved locally: {local_path}")

        return {
            "status": "success",
            "artifact_name": artifact_name,
            "local_path": str(local_path),
        }
    except Exception as e:
        print(f"[generate_image] Error: {e}")
        return {"status": "error", "message": str(e)}


def get_image() -> dict:
    """Return the path of the most recently generated image.

    Returns:
        A dict with 'status' and 'image_path' on success, or 'status' and
        'message' if no image has been generated yet.
    """
    path = _state.get("latest_image_path")
    if not path:
        return {"status": "error", "message": "No image has been generated yet."}
    return {"status": "success", "image_path": path}


def score_image_against_policy(image_path: str) -> dict:
    """Score the image at image_path against policy rules using GPT-4o vision.

    Args:
        image_path: Absolute path to the PNG image to score.

    Returns:
        A dict with 'total_score' (int) and 'scores' (per-category breakdown),
        or 'total_score': 0 and 'error' on failure.
    """
    import base64
    from openai import OpenAI

    policy_text = get_policy()["policy_text"]

    try:
        image_bytes = Path(image_path).read_bytes()
    except FileNotFoundError:
        return {"total_score": 0, "error": f"Image not found: {image_path}"}

    b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")
    scoring_prompt = (
        "You are an expert image evaluator for lockscreen content. "
        "Score this image against each of the following policy categories on a scale of 0-5 "
        "(5 = fully compliant, 0 = non-compliant).\n\n"
        "Return ONLY a valid JSON object with exactly two top-level keys:\n"
        '  "total_score": integer sum of all category scores\n'
        '  "scores": object mapping each category name to {"score": N, "reason": "..."}\n\n'
        "Categories to score (score each one):\n"
        "  - General Guidelines\n"
        "  - Global Defaults\n"
        "  - Media Type Definitions\n"
        "  - Image Specifications and Guidelines\n"
        "  - Text Specifications and Guidelines\n"
        "  - Clock Visibility\n"
        "  - Notification Area\n"
        "  - Safe Zones\n"
        "  - Composition Styles\n"
        "  - Color Scheme Definitions\n\n"
        f"Policy rules:\n{policy_text}\n\n"
        "Output ONLY the JSON object, no markdown, no explanation."
    )

    client = OpenAI()
    try:
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
                    {"type": "text", "text": scoring_prompt},
                ],
            }],
        )
        text = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        result = json.loads(text)
        print(f"[score_image_against_policy] total_score={result.get('total_score')}")
        return result
    except Exception as e:
        print(f"[score_image_against_policy] Error: {e}")
        return {"total_score": 0, "error": str(e)}


def set_score(total_score: int) -> str:
    """Persist the total image quality score to module-level state.

    Args:
        total_score: Integer score to store (sum of per-category scores, max 50).

    Returns:
        Confirmation string.
    """
    _state["total_score"] = total_score
    print(f"[set_score] total_score={total_score}")
    return f"Score {total_score} recorded."


def check_and_decide() -> str:
    """Increment loop counter and evaluate whether the pipeline should terminate.

    Mirrors the ADK check_condition_and_escalate_tool logic. Instead of setting
    tool_context.actions.escalate, returns a string ending with 'TERMINATE' when
    the RoundRobinGroup should stop (detected via termination_keyword in output[-20:]).

    Returns:
        A status string. Ends with 'TERMINATE' if the loop should stop,
        otherwise indicates that another round should follow.
    """
    _state["loop_iteration"] = _state.get("loop_iteration", 0) + 1
    iteration = _state["loop_iteration"]
    score     = _state.get("total_score", 0)

    print(f"[check_and_decide] iteration={iteration}, score={score}, "
          f"threshold={SCORE_THRESHOLD}, max={MAX_ITERATIONS}")

    if score > SCORE_THRESHOLD:
        return (
            f"Iteration {iteration}: score {score} exceeds threshold {SCORE_THRESHOLD}. "
            f"Image quality is acceptable. TERMINATE"
        )
    if iteration >= MAX_ITERATIONS:
        return (
            f"Iteration {iteration}: max iterations {MAX_ITERATIONS} reached. "
            f"Final score {score}. TERMINATE"
        )
    return (
        f"Iteration {iteration}: score {score} is below threshold {SCORE_THRESHOLD}. "
        f"Continuing to next iteration."
    )
