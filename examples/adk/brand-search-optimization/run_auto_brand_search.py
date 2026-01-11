#!/usr/bin/env python3
"""
Auto-run the brand_search_optimization agent with an LLM-driven user.

Each run:
  1) starts `adk run brand_search_optimization`
  2) watches for the [user]: prompt
  3) uses a driver LLM to craft replies in real time
  4) exits after completion or timeouts, then continues to next run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import random
import re
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import dotenv

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional dependency at runtime
    genai = None
    types = None


USER_PROMPT_TOKEN = "[user]:"
READY_PHRASES = (
    "Running agent brand_search_optimization",
    "type exit to exit",
)
BRAND_CHOICES = [
    "Penguin",
    "Nimbus",
    "Aurora",
    "Atlas",
    "Driftwood",
    "Cobalt",
    "Lumen",
    "Sierra",
    "Voyager",
    "Orchid",
]
KEYWORD_CHOICES = [
    "travel",
    "mystery",
    "historical fiction",
    "classics",
    "philosophy",
    "romance",
    "science fiction",
    "fantasy",
    "history",
    "art",
    "music",
    "poetry",
    "horror",
    "thriller",
]


@dataclass
class Scenario:
    brand_name: str
    website: str
    keyword: str
    search_request: str
    follow_up: str


@dataclass
class DriverState:
    brand_sent: bool = False
    search_request_sent: bool = False
    website_sent: bool = False
    keyword_sent: bool = False
    followup_sent: bool = False
    keywords_seen: bool = False
    comparison_seen: bool = False
    critic_seen: bool = False


def _build_scenario(
    rng: random.Random,
    website: str,
    brand: Optional[str],
    keyword: Optional[str],
) -> Scenario:
    # Keep defaults aligned with the example flow for reliability.
    return Scenario(
        brand_name=brand or rng.choice(BRAND_CHOICES),
        website=website,
        keyword=keyword or rng.choice(KEYWORD_CHOICES),
        search_request="okay search of keywords on website",
        follow_up="It is good / Please have the critic review this",
    )


def _build_client(use_vertex: bool) -> "genai.Client":
    if genai is None:
        raise RuntimeError("google-genai is not available in this environment.")
    if use_vertex:
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Vertex AI.")
        return genai.Client(vertexai=True, project=project, location=location)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is required for the API key flow.")
    return genai.Client(api_key=api_key)


def _system_prompt(scenario: Scenario, state: DriverState) -> str:
    return (
        "You are a simulated user for the brand search optimization demo. "
        "Use the scenario values to answer questions and keep the flow moving.\n\n"
        f"Scenario:\n"
        f"- brand_name: {scenario.brand_name}\n"
        f"- website: {scenario.website}\n"
        f"- keyword: {scenario.keyword}\n"
        f"- search_request: {scenario.search_request}\n"
        f"- follow_up: {scenario.follow_up}\n\n"
        "Rules:\n"
        "- Reply with a single line, no extra explanations.\n"
        "- If asked for brand, reply with brand_name.\n"
        "- If asked for website, reply with website.\n"
        "- If asked for keywords, reply with keyword.\n"
        "- After keywords are shown and the user prompt appears, ask to search "
        "the website using search_request (only once).\n"
        "- After a comparison report appears, reply with follow_up (only once).\n"
        "- After the critic response appears, reply with 'exit'.\n"
        f"\nState: comparison_seen={state.comparison_seen}, "
        f"critic_seen={state.critic_seen}, "
        f"keywords_seen={state.keywords_seen}, "
        f"search_request_sent={state.search_request_sent}, "
        f"followup_sent={state.followup_sent}\n"
    )


def _generate_reply(
    client: Optional["genai.Client"],
    model: str,
    scenario: Scenario,
    state: DriverState,
    conversation: list[tuple[str, str]],
    last_agent_text: str,
    llm_timeout: float,
) -> str:
    if not state.brand_sent:
        return scenario.brand_name
    if state.critic_seen:
        return "exit"
    if state.comparison_seen and not state.followup_sent:
        return scenario.follow_up
    if state.keywords_seen and not state.search_request_sent:
        return scenario.search_request
    if "keyword" in last_agent_text.lower():
        return scenario.keyword
    if "website" in last_agent_text.lower():
        state.website_sent = True
        return scenario.website
    if "brand" in last_agent_text.lower() and not state.brand_sent:
        return scenario.brand_name
    if client is None:
        raise RuntimeError("Driver LLM is required but not available.")

    recent = conversation[-12:]
    convo_text = "\n".join(f"{speaker}: {text}" for speaker, text in recent).strip()
    if not convo_text:
        convo_text = "(no conversation yet)"

    prompt = (
        "Conversation (most recent lines):\n"
        f"{convo_text}\n\n"
        "Your next user reply:"
    )
    system_prompt = _system_prompt(scenario, state)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _llm_generate_text,
                client,
                model,
                system_prompt,
                prompt,
            )
            text = (future.result(timeout=llm_timeout) or "").strip()
    except Exception:
        raise RuntimeError("Driver LLM failed or timed out.")

    if not text:
        raise RuntimeError("Driver LLM returned empty response.")
    reply = text.splitlines()[0].strip()
    if not reply:
        raise RuntimeError("Driver LLM returned empty response.")
    return reply


def _llm_generate_text(
    client: "genai.Client",
    model: str,
    system_prompt: str,
    prompt: str,
) -> str:
    if types is not None:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
            ),
        )
    else:
        response = client.models.generate_content(
            model=model, contents=f"{system_prompt}\n{prompt}"
        )
    return response.text or ""


def _read_available(master_fd: int, timeout: float) -> str:
    ready, _, _ = select.select([master_fd], [], [], timeout)
    if not ready:
        return ""
    try:
        data = os.read(master_fd, 4096)
    except OSError:
        return ""
    if not data:
        return ""
    return data.decode(errors="replace")


def _stream_until_ready(
    master_fd: int,
    ready_phrases: Iterable[str],
    timeout: float,
) -> str:
    buffer = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = _read_available(master_fd, 0.2)
        if not chunk:
            continue
        sys.stdout.write(chunk)
        sys.stdout.flush()
        buffer += chunk
        if any(phrase in buffer for phrase in ready_phrases):
            return buffer
    return buffer


def _extract_agent_line(line: str) -> Optional[tuple[str, str]]:
    match = re.match(r"^\[([^\]]+)\]:\s*(.*)$", line)
    if not match:
        return None
    return match.group(1), match.group(2)


def _line_mentions_keywords(line: str) -> bool:
    cleaned = line
    if cleaned.startswith("[") and "]:" in cleaned:
        cleaned = cleaned.split("]:", 1)[1].strip()
    lowered = cleaned.lower()
    if "ranked keywords" in lowered:
        return True
    if "keywords shoppers would type" in lowered:
        return True
    if "here are the keywords" in lowered:
        return True
    if lowered.startswith("| keyword"):
        return True
    if lowered.startswith("|") and "keyword" in lowered:
        return True
    return False


def _process_output_lines(
    buffer: str,
    conversation: list[tuple[str, str]],
    state: DriverState,
    last_agent_text: str,
    prompt_waiting: bool,
) -> tuple[str, str, bool]:
    while "\n" in buffer:
        line, buffer = buffer.split("\n", 1)
        line = line.rstrip()
        if not line:
            continue
        parsed = _extract_agent_line(line)
        if parsed:
            speaker, text = parsed
            if speaker == "user" and not text.strip():
                prompt_waiting = True
            else:
                conversation.append((speaker, text))
                if speaker != "user":
                    last_agent_text = text
        if _line_mentions_keywords(line):
            state.keywords_seen = True
        if "Comparison Report" in line:
            state.comparison_seen = True
        if "[comparison_critic_agent]" in line:
            state.critic_seen = True
    return buffer, last_agent_text, prompt_waiting


def _prompt_is_waiting(buffer: str) -> bool:
    idx = buffer.rfind(USER_PROMPT_TOKEN)
    if idx == -1:
        return False
    tail = buffer[idx + len(USER_PROMPT_TOKEN) :]
    return tail.strip() == ""


def run_once(
    client: Optional["genai.Client"],
    model: str,
    scenario: Scenario,
    idle_seconds: float,
    ready_seconds: float,
    hard_seconds: float,
    max_turns: int,
    llm_timeout: float,
) -> None:
    script_dir = Path(__file__).resolve().parent
    master_fd, slave_fd = os.openpty()

    proc = subprocess.Popen(
        ["adk", "run", "brand_search_optimization"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(script_dir),
        text=True,
        bufsize=0,
        close_fds=True,
    )
    os.close(slave_fd)

    conversation: list[tuple[str, str]] = []
    state = DriverState()
    buffer = ""
    last_output = time.time()
    start_time = last_output
    turns = 0
    last_agent_text = ""
    prompt_waiting = False

    try:
        buffer = _stream_until_ready(master_fd, READY_PHRASES, ready_seconds)
        last_output = time.time()
        buffer, last_agent_text, prompt_waiting = _process_output_lines(
            buffer, conversation, state, last_agent_text, prompt_waiting
        )
        while True:
            if proc.poll() is not None:
                return
            if time.time() - start_time > hard_seconds:
                os.kill(proc.pid, signal.SIGINT)
                return

            chunk = _read_available(master_fd, 0.2)
            if chunk:
                last_output = time.time()
                sys.stdout.write(chunk)
                sys.stdout.flush()
                buffer += chunk
                buffer, last_agent_text, prompt_waiting = _process_output_lines(
                    buffer, conversation, state, last_agent_text, prompt_waiting
                )

            if time.time() - last_output > idle_seconds:
                if state.search_request_sent and not state.comparison_seen:
                    continue
                os.kill(proc.pid, signal.SIGINT)
                return

            if prompt_waiting or _prompt_is_waiting(buffer):
                if turns >= max_turns:
                    os.write(master_fd, b"exit\n")
                    return
                try:
                    reply = _generate_reply(
                        client=client,
                        model=model,
                        scenario=scenario,
                        state=state,
                        conversation=conversation,
                        last_agent_text=last_agent_text,
                        llm_timeout=llm_timeout,
                    )
                except Exception as exc:
                    os.kill(proc.pid, signal.SIGINT)
                    raise exc
                if reply == scenario.search_request:
                    state.search_request_sent = True
                if reply == scenario.follow_up:
                    state.followup_sent = True
                if reply == scenario.brand_name:
                    state.brand_sent = True
                if reply == scenario.website:
                    state.website_sent = True
                if reply == scenario.keyword:
                    state.keyword_sent = True

                os.write(master_fd, (reply + "\n").encode())
                conversation.append(("user", reply))
                turns += 1
                buffer = ""
                prompt_waiting = False

    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-run brand_search_optimization with an LLM-driven user."
    )
    parser.add_argument(
        "-n", "--runs", type=int, default=1, help="number of runs to execute"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="run continuously until interrupted",
    )
    parser.add_argument(
        "--driver-model",
        default=os.getenv("MODEL", "gemini-2.5-flash"),
        help="model used by the driver LLM",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=60.0,
        help="send Ctrl+C after this many seconds of no output",
    )
    parser.add_argument(
        "--ready-seconds",
        type=float,
        default=30.0,
        help="wait up to this many seconds for the agent to start",
    )
    parser.add_argument(
        "--hard-seconds",
        type=float,
        default=360.0,
        help="force Ctrl+C after this many seconds no matter what",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=8,
        help="max user turns per run before sending exit",
    )
    parser.add_argument(
        "--driver-timeout",
        type=float,
        default=60.0,
        help="max seconds to wait for the driver LLM before falling back",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for scenario selection",
    )
    parser.add_argument(
        "--website",
        default="book store",
        help="website response used by the driver (default: book store)",
    )
    parser.add_argument(
        "--brand",
        default=None,
        help="force a specific brand name (default: random)",
    )
    parser.add_argument(
        "--keyword",
        default=None,
        help="force a specific keyword (default: random)",
    )
    args = parser.parse_args()

    dotenv.load_dotenv()
    rng = random.Random(args.seed)
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "0") in {"1", "true", "True"}

    try:
        client = _build_client(use_vertex)
    except Exception as exc:
        sys.stdout.write(f"Driver LLM unavailable: {exc}\n")
        return 1

    run_index = 0
    while True:
        if not args.loop and run_index >= args.runs:
            break
        run_index += 1
        scenario = _build_scenario(rng, args.website, args.brand, args.keyword)
        if args.loop:
            sys.stdout.write(f"\n=== Run {run_index} ===\n")
        else:
            sys.stdout.write(f"\n=== Run {run_index}/{args.runs} ===\n")
        sys.stdout.write(
            f"Scenario: brand={scenario.brand_name}, website={scenario.website}, "
            f"keyword={scenario.keyword}\n\n"
        )
        sys.stdout.flush()
        try:
            run_once(
                client=client,
                model=args.driver_model,
                scenario=scenario,
                idle_seconds=args.idle_seconds,
                ready_seconds=args.ready_seconds,
                hard_seconds=args.hard_seconds,
                max_turns=args.max_turns,
                llm_timeout=args.driver_timeout,
            )
        except KeyboardInterrupt:
            sys.stdout.write("\nInterrupted by user, stopping.\n")
            sys.stdout.flush()
            return 1
        except Exception as exc:
            sys.stdout.write(f"\nRun failed: {exc}\n")
            sys.stdout.flush()
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
