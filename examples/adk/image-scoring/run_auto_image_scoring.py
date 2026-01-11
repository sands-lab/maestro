#!/usr/bin/env python3
"""
Auto-run the image_scoring agent with randomized prompts.

Each run:
  1) starts `adk run image_scoring`
  2) sends one prompt
  3) waits until output goes idle, then sends SIGINT (Ctrl+C)
  4) continues to the next run
"""

from __future__ import annotations

import argparse
import os
import random
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


READY_PHRASES = (
    "Running agent",
    "type exit to exit",
)


def _generate_prompt(rng: random.Random) -> str:
    landscapes = ["peaceful", "serene", "misty", "golden", "dramatic"]
    times = ["sunset", "sunrise", "dusk"]
    animals = ["cat", "dog", "fox", "bear", "rabbit"]
    vehicles = ["bicycle", "scooter", "skateboard", "tricycle"]

    if rng.random() < 0.5:
        adjective = rng.choice(landscapes)
        time_of_day = rng.choice(times)
        return f"a {adjective} mountain landscape at {time_of_day}"
    animal = rng.choice(animals)
    vehicle = rng.choice(vehicles)
    return f"a {animal} riding a {vehicle}"


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
) -> None:
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
            return


def _stream_until_idle(
    master_fd: int,
    proc: subprocess.Popen[str],
    idle_seconds: float,
    hard_seconds: float,
) -> None:
    last_output = time.time()
    start_time = last_output
    while True:
        if proc.poll() is not None:
            return

        chunk = _read_available(master_fd, 0.2)
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            last_output = time.time()

        if time.time() - start_time > hard_seconds:
            os.kill(proc.pid, signal.SIGINT)
            return

        if time.time() - last_output > idle_seconds:
            os.kill(proc.pid, signal.SIGINT)
            return


def run_once(
    prompt: str,
    idle_seconds: float,
    ready_seconds: float,
    hard_seconds: float,
) -> None:
    script_dir = Path(__file__).resolve().parent
    master_fd, slave_fd = os.openpty()

    proc = subprocess.Popen(
        ["adk", "run", "image_scoring"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(script_dir),
        text=True,
        bufsize=0,
        close_fds=True,
    )
    os.close(slave_fd)

    try:
        _stream_until_ready(master_fd, READY_PHRASES, ready_seconds)
        os.write(master_fd, (prompt + "\n").encode())
        _stream_until_idle(master_fd, proc, idle_seconds, hard_seconds)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auto-run image_scoring with randomized prompts.",
    )
    parser.add_argument(
        "-n",
        "--runs",
        type=int,
        default=3,
        help="number of runs to execute",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=8.0,
        help="send Ctrl+C after this many seconds of no output",
    )
    parser.add_argument(
        "--ready-seconds",
        type=float,
        default=10.0,
        help="wait up to this many seconds for the agent to start",
    )
    parser.add_argument(
        "--hard-seconds",
        type=float,
        default=120.0,
        help="force Ctrl+C after this many seconds no matter what",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for reproducible prompts",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)

    for idx in range(args.runs):
        prompt = _generate_prompt(rng)
        sys.stdout.write(f"\n=== Run {idx + 1}/{args.runs} ===\n")
        sys.stdout.write(f"Prompt: {prompt}\n\n")
        sys.stdout.flush()
        try:
            run_once(
                prompt=prompt,
                idle_seconds=args.idle_seconds,
                ready_seconds=args.ready_seconds,
                hard_seconds=args.hard_seconds,
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
