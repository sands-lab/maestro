"""
OpenTelemetry observability tools for multi-agent systems.

This package provides utilities for analyzing and visualizing OpenTelemetry
trace and metrics data from multi-agent systems.
"""

from .lib.data_loaders import load_traces, load_metrics
from .lib.extractors import (
    extract_token_consumption,
    extract_delay_breakdown,
    extract_message_sizes,
    extract_cpu_memory_usage,
    extract_call_graph,
    extract_call_sequence,
)
from .lib.visualizers import (
    plot_token_consumption,
    plot_delay_breakdown,
    plot_cpu_memory_usage,
    plot_message_sizes,
    plot_events_with_cpu_memory,
    plot_latency_flame,
)
from .lib.analyzer import MetricsAnalyzer
from .lib.per_run_analyzer import analyze_per_run, plot_per_run_statistics
from .lib.comparison import (
    ExampleMetrics,
    MultiExampleCollector,
    CrossExampleVisualizer,
    compute_graph_edit_distance,
    compute_normalized_graph_edit_distance,
    compute_sequence_edit_distance,
    compute_normalized_sequence_edit_distance,
)

__all__ = [
    'load_traces',
    'load_metrics',
    'extract_token_consumption',
    'extract_delay_breakdown',
    'extract_message_sizes',
    'extract_cpu_memory_usage',
    'extract_call_graph',
    'extract_call_sequence',
    'plot_token_consumption',
    'plot_delay_breakdown',
    'plot_cpu_memory_usage',
    'plot_message_sizes',
    'plot_events_with_cpu_memory',
    'plot_latency_flame',
    'MetricsAnalyzer',
    'analyze_per_run',
    'plot_per_run_statistics',
    'ExampleMetrics',
    'MultiExampleCollector',
    'CrossExampleVisualizer',
    'compute_graph_edit_distance',
    'compute_normalized_graph_edit_distance',
    'compute_sequence_edit_distance',
    'compute_normalized_sequence_edit_distance',
]

__version__ = '0.1.0'
