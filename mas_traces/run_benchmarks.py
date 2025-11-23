"""Utility to run benchmark scenarios multiple times with timeouts."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


class BenchmarkConfig(Dict[str, object]):
    path: Path
    log_dir: Path
    command: List[str]


BENCHMARKS: Dict[str, BenchmarkConfig] = {
    "semantic_cache": {
        "path": ROOT / "semantic_cache_benchmark",
        "log_dir": ROOT / "semantic_cache_benchmark" / "logs",
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


def run_once(name: str, bench: BenchmarkConfig, timeout: int, run_number: int) -> int:
    bench_path = bench["path"]
    log_dir = bench["log_dir"]
    command = bench["command"]

    log_dir.mkdir(exist_ok=True, parents=True)
    before = {p.name for p in log_dir.iterdir() if p.is_file()}

    print(f"\n[{name}] Run {run_number}: executing {' '.join(command)} (timeout={timeout}s)")
    start = time.perf_counter()
    process = subprocess.Popen(command, cwd=bench_path)  # noqa: S603, S607

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list:
        list_benchmarks()
        return

    names = args.benchmarks or list(BENCHMARKS.keys())
    exit_code = 0

    for name in names:
        bench = BENCHMARKS[name]
        if not bench["path"].exists():
            print(f"[{name}] Skipping (path not found: {bench['path']})")
            exit_code = 1
            continue
        for run in range(1, args.runs + 1):
            exit_code |= run_once(name, bench, args.timeout, run)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

