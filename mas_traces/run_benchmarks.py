"""Utility to run benchmark scenarios multiple times with timeouts."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import os
import random
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


class BenchmarkConfig(Dict[str, object]):
    path: Path
    log_dir: Path
    command: List[str]


BENCHMARKS: Dict[str, BenchmarkConfig] = {
    "faq_redis_semantic_cache_naive": {
        "path": ROOT / "faq_redis_semantic_cache_naive",
        "log_dir": ROOT / "faq_redis_semantic_cache_naive" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "crag": {
        "path": ROOT / "crag",
        "log_dir": ROOT / "crag" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "tree_of_thoughts": {
        "path": ROOT / "tree-of-thoughts",
        "log_dir": ROOT / "tree-of-thoughts" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "language_agent_tree_search": {
        "path": ROOT / "language-agent-tree-search",
        "log_dir": ROOT / "language-agent-tree-search" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "plan_and_execute": {
        "path": ROOT / "Plan-and-Execute",
        "log_dir": ROOT / "Plan-and-Execute" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "tourist_scheduler": {
        "path": ROOT / "tourist_scheduler_benchmark",
        "log_dir": ROOT / "tourist_scheduler_benchmark" / "logs",
        "command": [PYTHON, "main.py"],
    },
    "mcp_financial": {
        "path": ROOT / "mcp_financial_analyzer_benchmark",
        "log_dir": ROOT / "mcp_financial_analyzer_benchmark" / "logs",
        "command": [PYTHON, "main.py"],
    },
}


def list_benchmarks() -> None:
    print("Available benchmarks:")
    for key in BENCHMARKS:
        print(f" - {key}")


def run_once(
    name: str,
    bench: BenchmarkConfig,
    timeout: int,
    run_number: int,
    env_overrides: Dict[str, str],
) -> int:
    bench_path = bench["path"]
    log_dir = bench["log_dir"]
    command = bench["command"]

    log_dir.mkdir(exist_ok=True, parents=True)
    before = {p.name for p in log_dir.iterdir() if p.is_file()}
    env = os.environ.copy()
    env.update(env_overrides)
    if env_overrides.get("BENCHMARK_LLM_REQUESTS_PER_MIN") and name == "mcp_financial":
        env.setdefault("GOOGLE_RATE_LIMIT_REQUESTS", env_overrides["BENCHMARK_LLM_REQUESTS_PER_MIN"])
        if env_overrides.get("BENCHMARK_LLM_RATE_PERIOD"):
            env.setdefault("GOOGLE_RATE_LIMIT_PERIOD_SECONDS", env_overrides["BENCHMARK_LLM_RATE_PERIOD"])

    print(f"\n[{name}] Run {run_number}: executing {' '.join(command)} (timeout={timeout}s)")
    start = time.perf_counter()
    process = subprocess.Popen(command, cwd=bench_path, env=env)  # noqa: S603, S607

    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()

    duration = time.perf_counter() - start
    status = "timeout" if timed_out else ("ok" if process.returncode == 0 else f"exit {process.returncode}")
    after = {p.name for p in log_dir.iterdir() if p.is_file()}
    new_logs = sorted(after - before)

    print(f"[{name}] Run {run_number} completed in {duration:.1f}s -> {status}")
    if new_logs:
        print(f"[{name}] New logs: {', '.join(new_logs)}")
    else:
        print(f"[{name}] No new log files detected in {log_dir}")

    return 0 if (not timed_out and process.returncode == 0) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run benchmark suites with timeouts.")
    parser.add_argument(
        "--benchmark",
        dest="benchmarks",
        choices=BENCHMARKS.keys(),
        action="append",
        help="Benchmark name to run (can be provided multiple times). Defaults to all.",
    )
    parser.add_argument("--runs", type=int, default=1, help="Number of times to run each benchmark.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout (in seconds) per run. Processes are SIGKILLed on timeout.",
    )
    parser.add_argument("--list", action="store_true", help="List available benchmarks and exit.")
    parser.add_argument(
        "--llm-rate-limit",
        type=float,
        default=None,
        help="Optional LLM requests-per-minute ceiling applied to compatible benchmarks.",
    )
    parser.add_argument(
        "--llm-rate-period",
        type=float,
        default=60.0,
        help="Window size (seconds) used when enforcing the LLM rate limit.",
    )
    parser.add_argument(
        "--rest-min",
        type=float,
        default=0.0,
        help="Minimum rest interval (minutes) inserted between runs.",
    )
    parser.add_argument(
        "--rest-max",
        type=float,
        default=0.0,
        help="Maximum rest interval (minutes) inserted between runs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list:
        list_benchmarks()
        return

    names = args.benchmarks or list(BENCHMARKS.keys())
    exit_code = 0

    llm_overrides: Dict[str, str] = {}
    if args.llm_rate_limit:
        llm_overrides["BENCHMARK_LLM_REQUESTS_PER_MIN"] = str(args.llm_rate_limit)
        if args.llm_rate_period:
            llm_overrides["BENCHMARK_LLM_RATE_PERIOD"] = str(args.llm_rate_period)

    for name in names:
        bench = BENCHMARKS[name]
        if not bench["path"].exists():
            print(f"[{name}] Skipping (path not found: {bench['path']})")
            exit_code = 1
            continue
        for run in range(1, args.runs + 1):
            exit_code |= run_once(name, bench, args.timeout, run, llm_overrides)
            if run != args.runs and args.rest_max > 0:
                low = max(0.0, min(args.rest_min, args.rest_max))
                high = max(args.rest_min, args.rest_max)
                delay = random.uniform(low, high) * 60.0
                if delay > 0:
                    print(f"[{name}] Resting for {delay/60.0:.2f} minutes before next run...")
                    time.sleep(delay)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
