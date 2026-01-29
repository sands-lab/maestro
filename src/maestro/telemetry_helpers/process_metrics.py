"""Utility helpers for estimating per-process CPU and RSS metrics."""

from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any, Optional

try:  # pragma: no cover - optional dependency
    import resource  # type: ignore
except Exception:  # pragma: no cover
    resource = None  # type: ignore

_PROC_STATM = Path("/proc/self/statm")
_IS_DARWIN = platform.system() == "Darwin"


class CpuUsageSampler:
    """Returns CPU usage deltas either via psutil or stdlib fallbacks."""

    def __init__(self, process: Optional[Any] = None) -> None:
        self._process = process
        self._cpu_count = max(1, os.cpu_count() or 1)
        self._last_cpu_time: float | None = None
        self._last_wall_time: float | None = None
        if self._process is not None:
            self._prime_process_sampler()

    def read_percent(self) -> float:
        if self._process is not None:
            try:
                return float(self._process.cpu_percent(interval=None))
            except Exception:
                # Fall back to portable tracking if psutil stops working.
                self._process = None
        return self._read_with_resource()

    def _prime_process_sampler(self) -> None:
        try:
            self._process.cpu_percent(interval=None)
        except Exception:
            self._process = None

    def _read_with_resource(self) -> float:
        cpu_time = self._read_process_time()
        if cpu_time is None:
            return 0.0
        now = time.time()
        last_cpu = self._last_cpu_time
        last_wall = self._last_wall_time
        self._last_cpu_time = cpu_time
        self._last_wall_time = now
        if last_cpu is None or last_wall is None:
            return 0.0
        wall_delta = now - last_wall
        if wall_delta <= 0:
            return 0.0
        cpu_delta = cpu_time - last_cpu
        if cpu_delta <= 0:
            return 0.0
        usage = (cpu_delta / wall_delta) * 100.0 / self._cpu_count
        if usage < 0:
            return 0.0
        return usage

    def _read_process_time(self) -> float | None:
        if resource is not None:
            try:
                usage = resource.getrusage(resource.RUSAGE_SELF)
                return float(usage.ru_utime + usage.ru_stime)
            except Exception:
                pass
        try:
            return float(time.process_time())
        except Exception:
            return None


def read_rss_bytes_fallback() -> float:
    """
    Return the current resident set size without psutil if possible.

    Linux exposes /proc/self/statm which reports the current RSS in pages.
    When that is unavailable we fall back to resource.getrusage, which
    returns the historical high-water mark and therefore never decreases.
    """

    rss = _read_proc_statm_rss()
    if rss is not None:
        return rss
    if resource is not None:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            value = float(usage.ru_maxrss)
            if os.name == "posix" and not _IS_DARWIN:
                value *= 1024.0
            return value
        except Exception:
            return 0.0
    return 0.0


def _read_proc_statm_rss() -> float | None:
    if not _PROC_STATM.exists():
        return None
    try:
        contents = _PROC_STATM.read_text().split()
        if len(contents) < 2:
            return None
        resident_pages = int(contents[1])
        page_size = _get_page_size()
        return float(resident_pages * page_size)
    except Exception:
        return None


def _get_page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError):
        pass
    if resource is not None and hasattr(resource, "getpagesize"):
        try:
            return int(resource.getpagesize())  # type: ignore[attr-defined]
        except Exception:
            pass
    return 4096
