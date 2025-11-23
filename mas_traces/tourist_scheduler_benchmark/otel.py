"""OpenTelemetry helpers for file-based span export."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult, SpanExporter


class FileSpanExporter(SpanExporter):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def export(self, spans) -> SpanExportResult:  # type: ignore[override]
        lines = []
        for span in spans:
            lines.append(
                json.dumps(
                    {
                        "name": span.name,
                        "trace_id": format(span.context.trace_id, "032x"),
                        "span_id": format(span.context.span_id, "016x"),
                        "attributes": dict(span.attributes),
                        "start_time": span.start_time,
                        "end_time": span.end_time,
                    }
                )
            )
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - no cleanup needed
        return None


def setup_tracer(log_dir: Path) -> tuple[trace.Tracer, Path, TracerProvider]:
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"run_{timestamp}.log"

    provider = TracerProvider(resource=Resource.create({"service.name": "tourist-scheduler-benchmark"}))
    exporter = FileSpanExporter(log_path)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("tourist-scheduler-benchmark")
    return tracer, log_path, provider

