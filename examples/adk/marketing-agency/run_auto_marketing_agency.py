#!/usr/bin/env python3
"""
Auto-run the marketing_agency agent with an LLM-driven user.

Each run:
  1) starts `adk run marketing_agency`
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
    "Running agent marketing",
    "type exit to exit",
)


@dataclass
class Scenario:
    domain_keywords: str
    domain_choice: str
    brand_name: str
    website_goal: str
    key_services: str
    product_details: str
    usp: str
    marketing_goal: str
    target_audience: str

    def website_info(self) -> str:
        return (
            f'Brand/Project Name: "{self.brand_name}". '
            f"Primary goal: {self.website_goal}. "
            f"Key services: {self.key_services}."
        )

    def marketing_info(self) -> str:
        return (
            f"Product/Service Details: {self.product_details}. "
            f"USP: {self.usp}. "
            f"Primary Marketing Goal(s): {self.marketing_goal}. "
            f"Target Audience Profile: {self.target_audience}."
        )


@dataclass
class DriverState:
    greeting_sent: bool = False
    pitch_sent: bool = False
    domain_options_seen: bool = False
    domain_choice_sent: bool = False
    website_info_needed: bool = False
    website_info_sent: bool = False
    marketing_info_needed: bool = False
    marketing_info_sent: bool = False
    strategy_seen: bool = False
    logo_prompt_seen: bool = False
    logo_reply_sent: bool = False
    completion_seen: bool = False


class DriverTimeoutError(RuntimeError):
    pass


SCENARIOS = [
    Scenario(
        domain_keywords="organic cakes, bakery, fresh desserts",
        domain_choice="9",
        brand_name="Aurora Organic Cakes",
        website_goal="Showcase organic cakes and accept custom orders online",
        key_services="wedding cakes, birthday cakes, vegan options, gallery, order form",
        product_details="fresh organic ingredients, made-to-order cakes, local delivery",
        usp="100% organic ingredients with artistic custom designs and fast consults",
        marketing_goal="Reach 50 orders per month and grow Instagram to 10k followers",
        target_audience=(
            "18-30 in Europe, health-conscious foodies, active on Instagram/TikTok, "
            "value sustainability and aesthetic presentation"
        ),
    ),
    Scenario(
        domain_keywords="handmade jewelry, minimalist gifts, custom engraving",
        domain_choice="1",
        brand_name="Lumen Handmade Jewelry",
        website_goal="Sell handmade jewelry and capture custom order inquiries",
        key_services="necklaces, rings, bracelets, custom engraving, lookbook",
        product_details="sterling silver pieces, small-batch production, custom sizing",
        usp="sustainable materials with personalized engraving and gift packaging",
        marketing_goal="Increase online sales by 20% and grow the email list to 2k",
        target_audience=(
            "25-40, urban professionals and gift buyers, Etsy shoppers, "
            "value craftsmanship and sustainable materials"
        ),
    ),
    Scenario(
        domain_keywords="online fitness coaching, strength training, nutrition plans",
        domain_choice="5",
        brand_name="Atlas Fit Coaching",
        website_goal="Generate leads for coaching programs and book consultations",
        key_services="1:1 coaching, workout plans, nutrition guidance, testimonials",
        product_details="personalized training plans with weekly check-ins",
        usp="data-driven programming combined with habit coaching",
        marketing_goal="Book 15 new clients per month and raise lead conversion rate",
        target_audience=(
            "20-35, busy professionals seeking structured fitness, "
            "active on YouTube and Instagram, value accountability"
        ),
    ),
]


def _build_scenario(rng: random.Random, scenario_index: Optional[int]) -> Scenario:
    if scenario_index is not None:
        idx = max(0, min(scenario_index, len(SCENARIOS) - 1))
        return SCENARIOS[idx]
    return rng.choice(SCENARIOS)


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
        "You are a simulated user for the marketing agency demo. "
        "Use the scenario values to answer questions and keep the flow moving.\n\n"
        f"Scenario:\n"
        f"- domain_keywords: {scenario.domain_keywords}\n"
        f"- domain_choice: {scenario.domain_choice}\n"
        f"- brand_name: {scenario.brand_name}\n"
        f"- website_goal: {scenario.website_goal}\n"
        f"- key_services: {scenario.key_services}\n"
        f"- product_details: {scenario.product_details}\n"
        f"- usp: {scenario.usp}\n"
        f"- marketing_goal: {scenario.marketing_goal}\n"
        f"- target_audience: {scenario.target_audience}\n\n"
        "Rules:\n"
        "- Reply with a single line, no extra explanations.\n"
        "- Never ask questions. Do not say you are an assistant or AI.\n"
        "- If asked for keywords, reply with domain_keywords.\n"
        "- If asked to choose a domain, reply with domain_choice.\n"
        "- If asked for website details, reply with brand/goal/services.\n"
        "- If asked for marketing strategy inputs, reply with product/USP/goal/audience.\n"
        "- If asked about creating a logo, prefer continuing with logo creation.\n"
        f"\nState: strategy_seen={state.strategy_seen}, "
        f"domain_options_seen={state.domain_options_seen}, "
        f"website_info_needed={state.website_info_needed}, "
        f"marketing_info_needed={state.marketing_info_needed}\n"
    )


def _generate_reply(
    client: Optional["genai.Client"],
    model: str,
    scenario: Scenario,
    state: DriverState,
    conversation: list[tuple[str, str]],
    last_agent_text: str,
    llm_timeout: float,
    guard_mode: str,
    logo_bias: bool,
) -> str:
    if state.completion_seen:
        return "exit"
    if not state.greeting_sent:
        return "hi"
    if guard_mode == "soft":
        if state.strategy_seen:
            return "exit"
        if state.domain_options_seen and not state.domain_choice_sent:
            return scenario.domain_choice
        if state.website_info_needed and not state.website_info_sent:
            return scenario.website_info()
        if state.marketing_info_needed and not state.marketing_info_sent:
            return scenario.marketing_info()

        lowered = last_agent_text.lower()
        if "keyword" in lowered:
            return scenario.domain_keywords
        if "domain" in lowered and ("choose" in lowered or "select" in lowered):
            return scenario.domain_choice
        if "brand/project name" in lowered or "brand name" in lowered:
            return scenario.brand_name
        if "primary goal" in lowered or "purpose of the website" in lowered:
            return scenario.website_goal
        if "key services" in lowered or "products" in lowered:
            return scenario.key_services
        if "product/service details" in lowered:
            return scenario.product_details
        if "usp" in lowered or "unique selling" in lowered:
            return scenario.usp
        if "marketing goal" in lowered:
            return scenario.marketing_goal
        if "target audience" in lowered:
            return scenario.target_audience

    if logo_bias and state.logo_prompt_seen and not state.logo_reply_sent:
        return "yes, please proceed with the logo"

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
    except concurrent.futures.TimeoutError:
        raise DriverTimeoutError(f"Driver LLM timeout after {llm_timeout}s.")
    except Exception:
        raise RuntimeError("Driver LLM failed.")

    if not text:
        raise RuntimeError("Driver LLM returned empty response.")
    reply = text.splitlines()[0].strip()
    if not reply:
        raise RuntimeError("Driver LLM returned empty response.")

    if _reply_looks_like_question(reply):
        return _fallback_reply_for_state(
            scenario, state, last_agent_text, logo_bias
        )

    lowered = reply.lower()
    if "i am" in lowered and "assistant" in lowered:
        return _fallback_reply_for_state(
            scenario, state, last_agent_text, logo_bias
        )
    if "i'm" in lowered and "assistant" in lowered:
        return _fallback_reply_for_state(
            scenario, state, last_agent_text, logo_bias
        )

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


def _reply_looks_like_question(reply: str) -> bool:
    lowered = reply.strip().lower()
    if "?" in lowered:
        return True
    starters = (
        "what ",
        "why ",
        "how ",
        "who ",
        "which ",
        "could you",
        "can you",
        "would you",
        "please ",
    )
    return lowered.startswith(starters)


def _fallback_reply_for_state(
    scenario: Scenario,
    state: DriverState,
    last_agent_text: str,
    logo_bias: bool,
) -> str:
    if state.completion_seen:
        return "exit"
    if logo_bias and state.logo_prompt_seen and not state.logo_reply_sent:
        return "yes, please proceed with the logo"
    if not state.pitch_sent:
        return scenario.domain_keywords
    if state.domain_options_seen and not state.domain_choice_sent:
        return scenario.domain_choice
    if state.website_info_needed and not state.website_info_sent:
        return scenario.website_info()
    if state.marketing_info_needed and not state.marketing_info_sent:
        return scenario.marketing_info()

    lowered = last_agent_text.lower()
    if "keyword" in lowered:
        return scenario.domain_keywords
    if "domain" in lowered and ("choose" in lowered or "select" in lowered):
        return scenario.domain_choice
    if "brand/project name" in lowered or "brand name" in lowered:
        return scenario.brand_name
    if "primary goal" in lowered or "purpose of the website" in lowered:
        return scenario.website_goal
    if "key services" in lowered or "products" in lowered:
        return scenario.key_services
    if "product/service details" in lowered:
        return scenario.product_details
    if "usp" in lowered or "unique selling" in lowered:
        return scenario.usp
    if "marketing goal" in lowered:
        return scenario.marketing_goal
    if "target audience" in lowered:
        return scenario.target_audience

    return scenario.domain_keywords


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


def _clean_agent_line(line: str) -> str:
    if line.startswith("[") and "]:" in line:
        return line.split("]:", 1)[1].strip()
    return line


def _line_mentions_domain_options(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    if "domain" in lowered and ("options" in lowered or "available" in lowered):
        return True
    if "domain_create" in lowered:
        return True
    if ".com" in lowered and "domain" in lowered:
        return True
    return False


def _line_requests_website_info(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    if "brand/project name" in lowered:
        return True
    if "primary goal" in lowered:
        return True
    if "key services" in lowered:
        return True
    if "website creation" in lowered and "missing" in lowered:
        return True
    return False


def _line_requests_marketing_info(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    if "product/service details" in lowered:
        return True
    if "primary marketing goal" in lowered:
        return True
    if "target audience" in lowered:
        return True
    if "marketing strategy" in lowered and "missing" in lowered:
        return True
    return False


def _line_mentions_strategy(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    if "executive summary" in lowered:
        return True
    if "core marketing strategy" in lowered:
        return True
    if "recommended marketing channels" in lowered:
        return True
    if "marketing strategy" in lowered and "strategy" in lowered:
        return True
    return False


def _line_mentions_logo_prompt(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    if "logo" not in lowered:
        return False
    if "proceed" in lowered or "create" in lowered or "design" in lowered:
        return True
    return lowered.endswith("?")


def _line_mentions_completion(line: str) -> bool:
    lowered = _clean_agent_line(line).lower()
    endings = (
        "we have successfully",
        "we have now completed",
        "we've now completed",
        "we have completed",
        "this completes",
        "all tasks",
        "best of luck",
        "goodbye",
        "start a new conversation",
    )
    return any(phrase in lowered for phrase in endings)


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
        if _line_mentions_domain_options(line):
            state.domain_options_seen = True
        if _line_requests_website_info(line):
            state.website_info_needed = True
        if _line_requests_marketing_info(line):
            state.marketing_info_needed = True
        if _line_mentions_strategy(line):
            state.strategy_seen = True
        if _line_mentions_logo_prompt(line):
            state.logo_prompt_seen = True
        if _line_mentions_completion(line):
            state.completion_seen = True
    return buffer, last_agent_text, prompt_waiting


def _list_artifact_paths(base_dir: Path) -> set[Path]:
    paths: set[Path] = set()
    for subdir in ("metrics", "traces"):
        folder = base_dir / subdir
        if not folder.exists():
            continue
        paths.update(path for path in folder.glob("*.jsonl") if path.is_file())
    return paths


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
    guard_mode: str,
    logo_bias: bool,
) -> None:
    script_dir = Path(__file__).resolve().parent
    master_fd, slave_fd = os.openpty()

    proc = subprocess.Popen(
        ["adk", "run", "marketing_agency"],
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
            if hard_seconds > 0 and time.time() - start_time > hard_seconds:
                sys.stdout.write(
                    f"\n[auto] Hard timeout after {hard_seconds:.0f}s, stopping run.\n"
                )
                sys.stdout.flush()
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

            if idle_seconds > 0 and time.time() - last_output > idle_seconds:
                if state.website_info_sent and not state.marketing_info_needed and not state.strategy_seen:
                    continue
                if state.marketing_info_sent and not state.strategy_seen:
                    continue
                sys.stdout.write(
                    f"\n[auto] Idle timeout after {idle_seconds:.0f}s, stopping run.\n"
                )
                sys.stdout.flush()
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
                        guard_mode=guard_mode,
                        logo_bias=logo_bias,
                    )
                except Exception as exc:
                    os.kill(proc.pid, signal.SIGINT)
                    raise exc

                if reply == scenario.domain_keywords:
                    state.pitch_sent = True
                if reply.strip().lower() == "hi":
                    state.greeting_sent = True
                if reply == scenario.domain_choice:
                    state.domain_choice_sent = True
                if reply == scenario.website_info():
                    state.website_info_sent = True
                if reply == scenario.marketing_info():
                    state.marketing_info_sent = True
                if state.logo_prompt_seen and not state.logo_reply_sent:
                    state.logo_reply_sent = True

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
        description="Auto-run marketing_agency with an LLM-driven user."
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
        "--scenario",
        type=int,
        default=None,
        help="use a fixed scenario index (0-based)",
    )
    parser.add_argument(
        "--driver-model",
        default=os.getenv("MODEL", "gemini-2.5-flash"),
        help="model used by the driver LLM",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=600.0,
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
        default=600.0,
        help="force Ctrl+C after this many seconds no matter what",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=15,
        help="max user turns per run before sending exit",
    )
    parser.add_argument(
        "--driver-timeout",
        type=float,
        default=60.0,
        help="max seconds to wait for the driver LLM before failing",
    )
    parser.add_argument(
        "--no-timeouts",
        action="store_true",
        help="disable idle and hard timeouts",
    )
    parser.add_argument(
        "--guard",
        choices=["soft", "off"],
        default="off",
        help="guard mode for deterministic replies (default: soft)",
    )
    parser.add_argument(
        "--logo-bias",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prefer proceeding with logo creation when prompted",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for scenario selection",
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

    if args.no_timeouts:
        args.idle_seconds = 0
        args.hard_seconds = 0

    run_index = 0
    success_count = 0
    timeout_failures = 0
    script_dir = Path(__file__).resolve().parent
    while True:
        if not args.loop and success_count >= args.runs:
            break
        run_index += 1
        scenario = _build_scenario(rng, args.scenario)
        if args.loop:
            sys.stdout.write(f"\n=== Run {run_index} ===\n")
        else:
            sys.stdout.write(
                f"\n=== Run {success_count + 1}/{args.runs} (attempt {run_index}) ===\n"
            )
        sys.stdout.write(
            f"Scenario: brand={scenario.brand_name}, keywords={scenario.domain_keywords}\n\n"
        )
        sys.stdout.flush()
        artifacts_before = _list_artifact_paths(script_dir)
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
                guard_mode=args.guard,
                logo_bias=args.logo_bias,
            )
            success_count += 1
        except KeyboardInterrupt:
            sys.stdout.write("\nInterrupted by user, stopping.\n")
            sys.stdout.flush()
            return 1
        except DriverTimeoutError as exc:
            timeout_failures += 1
            artifacts_after = _list_artifact_paths(script_dir)
            for path in sorted(artifacts_after - artifacts_before):
                try:
                    path.unlink()
                except OSError:
                    continue
            sys.stdout.write(f"\nRun failed: {exc}\n")
            if timeout_failures > 5:
                sys.stdout.write(
                    "\nDriver LLM timed out more than 5 times; stopping.\n"
                )
                sys.stdout.flush()
                return 1
            sys.stdout.flush()
            continue
        except Exception as exc:
            sys.stdout.write(f"\nRun failed: {exc}\n")
            sys.stdout.flush()
            continue

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
