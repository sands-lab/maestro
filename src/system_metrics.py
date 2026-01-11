"""
Background system metrics monitor that writes OTEL-style metric snapshots locally.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:  # pragma: no cover - psutil is optional at runtime
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

try:  # pragma: no cover
    from opentelemetry.sdk.version import __version__ as OTEL_SDK_VERSION  # type: ignore
except Exception:  # pragma: no cover
    OTEL_SDK_VERSION = "unknown"

from .process_metrics import CpuUsageSampler, read_rss_bytes_fallback


@dataclass(slots=True)
class MetricData:
    metric_name: str
    description: str
    unit: str
    value: float


class SystemMetricsMonitor:
    """Periodically records CPU/memory stats without touching existing OTEL exporters."""

    def __init__(
        self,
        service_name: str,
        output_dir: str = "metrics",
        interval_seconds: float = 15.0,
        instrumentation_scope: str = "system-metrics-monitor",
        environment: str | None = None,
        logger: Optional[Any] = None,
        run_id: str | None = None,
    ):
        self.service_name = service_name
        self.output_dir = Path(output_dir)
        self.interval_seconds = max(1.0, interval_seconds)
        self.instrumentation_scope = instrumentation_scope
        self.environment = environment or os.getenv("DEPLOYMENT_ENVIRONMENT", "local")
        self.logger = logger
        self.run_id = run_id

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._process = self._init_process()
        self._cpu_sampler = CpuUsageSampler(self._process)
        self._fixed_metrics_path = None
        if self.run_id:
            self._fixed_metrics_path = (
                self.output_dir / f"system_metrics_{self.run_id}.jsonl"
            )
        self._resource_attributes = {
            "telemetry.sdk.language": "python",
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.version": OTEL_SDK_VERSION,
            "service.name": self.service_name,
            "service.version": os.getenv("SERVICE_VERSION", "0.0.0"),
            "deployment.environment": self.environment,
        }

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.stop()

    def start(self):
        if self._task:
            return
        loop = asyncio.get_running_loop()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._task = loop.create_task(self._run())
        if self.logger:
            self.logger.debug(
                "SystemMetricsMonitor started with interval %.1fs", self.interval_seconds
            )

    async def stop(self):
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        if self.logger:
            self.logger.debug("SystemMetricsMonitor stopped")

    async def _run(self):
        while not self._stop_event.is_set():
            try:
                await self._write_snapshot()
            except Exception as error:  # pragma: no cover - defensive logging
                if self.logger:
                    self.logger.warning(
                        "SystemMetricsMonitor failed to write snapshot: %s", error
                    )
            await self._wait_for_interval()

    async def _wait_for_interval(self):
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
        except asyncio.TimeoutError:
            return

    async def _write_snapshot(self):
        metrics = self._collect_metrics()
        timestamp = datetime.now(timezone.utc)
        payload = [
            self._to_otel_payload(metric, timestamp)
            for metric in metrics
            if metric.value is not None
        ]
        if self._fixed_metrics_path:
            self._fixed_metrics_path.parent.mkdir(parents=True, exist_ok=True)
            with self._fixed_metrics_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload))
                handle.write("\n")
        else:
            filename = f"system_metrics_{timestamp.strftime('%Y%m%dT%H%M%S_%fZ')}.json"
            path = self.output_dir / filename
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _collect_metrics(self) -> list[MetricData]:
        metrics: list[MetricData] = []
        metrics.append(
            MetricData(
                metric_name="process.cpu.usage",
                description="Process CPU usage percentage",
                unit="%",
                value=self._read_cpu_percent(),
            )
        )
        metrics.append(
            MetricData(
                metric_name="process.memory.usage_bytes",
                description="Process memory usage in bytes",
                unit="bytes",
                value=self._read_memory_usage(),
            )
        )
        return metrics

    def _read_cpu_percent(self) -> float:
        return float(self._cpu_sampler.read_percent())

    def _read_memory_usage(self) -> float:
        if self._process and psutil:
            try:
                return float(self._process.memory_info().rss)
            except Exception:
                pass
        return read_rss_bytes_fallback()

    def _to_otel_payload(self, metric: MetricData, timestamp: datetime) -> dict[str, Any]:
        timestamp_ns = time.time_ns()
        data_point = {
            "value": metric.value,
            "timestamp": timestamp_ns,
            "attributes": {
                "agent.name": self.service_name,
            },
        }
        return {
            "timestamp": timestamp.isoformat(),
            "metric_name": metric.metric_name,
            "description": metric.description,
            "unit": metric.unit,
            "data_points": [data_point],
            "resource": {"attributes": self._resource_attributes},
            "scope": self.instrumentation_scope,
        }

    def _init_process(self):
        if not psutil:
            return None
        try:
            process = psutil.Process(os.getpid())
            process.cpu_percent(interval=None)
            return process
        except Exception:
            return None
