"""Tools for the Stock Research AutoGen demo in mas_creator."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List

try:
    from opentelemetry import metrics, trace
except Exception:
    metrics = None
    trace = None


class _NoOpCounter:
    """No-op counter used when OpenTelemetry is unavailable."""

    def add(self, *_args: Any, **_kwargs: Any) -> None:
        """No-op add method."""


class _NoOpHistogram:
    """No-op histogram used when OpenTelemetry is unavailable."""

    def record(self, *_args: Any, **_kwargs: Any) -> None:
        """No-op record method."""


class _NoOpTracer:
    """No-op tracer used when OpenTelemetry is unavailable."""

    @contextmanager
    def start_as_current_span(self, _name: str):
        """Yield a no-op span context manager."""

        class _NoOpSpan:
            def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
                """No-op attribute setter."""

        yield _NoOpSpan()


if metrics is not None and trace is not None:
    tracer = trace.get_tracer("stock-research-custom-tracer")
    meter = metrics.get_meter("stock-research-custom-metrics")
    message_size_histogram = meter.create_histogram(
        name="autogen.agent.message_size",
        description="Size of messages processed by AutoGen agents",
        unit="bytes",
    )
    tool_call_counter = meter.create_counter(
        name="stock_research.tools.calls",
        description="Number of tool calls made during research",
    )
else:
    tracer = _NoOpTracer()
    message_size_histogram = _NoOpHistogram()
    tool_call_counter = _NoOpCounter()


async def get_stock_data(symbol: str) -> Dict[str, Any]:
    """Get stock market data for a given symbol"""
    with tracer.start_as_current_span("get_stock_data") as span:
        span.set_attribute("tool.name", "get_stock_data")
        span.set_attribute("stock.symbol", symbol)

        # Record tool call
        tool_call_counter.add(1, {"tool": "get_stock_data", "symbol": symbol})

        # Simulate data retrieval
        data = {
            "price": 180.25,
            "volume": 1000000,
            "pe_ratio": 65.4,
            "market_cap": "700B",
        }

        # Record response size
        response_size = len(str(data).encode("utf-8"))
        message_size_histogram.record(
            response_size, {"message_type": "tool_response", "tool": "get_stock_data"}
        )
        span.set_attribute("response.size", response_size)

        return data


async def get_news(query: str) -> List[Dict[str, str]]:
    """Get recent news articles about a company"""
    with tracer.start_as_current_span("get_news") as span:
        span.set_attribute("tool.name", "get_news")
        span.set_attribute("news.query", query)

        # Record tool call
        tool_call_counter.add(1, {"tool": "get_news", "query": query})

        # Simulate news data
        news_data = [
            {
                "title": "Tesla Expands Cybertruck Production",
                "date": "2024-03-20",
                "summary": "Tesla ramps up Cybertruck manufacturing capacity at Gigafactory Texas, aiming to meet strong demand.",
            },
            {
                "title": "Tesla FSD Beta Shows Promise",
                "date": "2024-03-19",
                "summary": "Latest Full Self-Driving beta demonstrates significant improvements in urban navigation and safety features.",
            },
            {
                "title": "Model Y Dominates Global EV Sales",
                "date": "2024-03-18",
                "summary": "Tesla's Model Y becomes best-selling electric vehicle worldwide, capturing significant market share.",
            },
        ]

        # Record response size
        response_size = len(str(news_data).encode("utf-8"))
        message_size_histogram.record(
            response_size, {"message_type": "tool_response", "tool": "get_news"}
        )
        span.set_attribute("response.size", response_size)
        span.set_attribute("news.count", len(news_data))

        return news_data
