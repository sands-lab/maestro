"""Main analyzer class for OpenTelemetry metrics analysis."""

import glob
import re
from pathlib import Path
from typing import Iterable, Optional, List, Dict

from .data_loaders import load_traces, load_metrics
from .extractors import (
    extract_token_consumption,
    extract_delay_breakdown,
    extract_message_sizes,
    extract_cpu_memory_usage,
)
from .visualizers import (
    plot_token_consumption,
    plot_delay_breakdown,
    plot_cpu_memory_usage,
    plot_message_sizes,
    plot_events_with_cpu_memory,
    plot_latency_flame,
)
from .per_run_analyzer import analyze_per_run, plot_per_run_statistics


class MetricsAnalyzer:
    """Main class for analyzing and visualizing OpenTelemetry metrics."""

    def __init__(
        self,
        trace_file: Optional[str] = None,
        metrics_file: Optional[str] = None,
        traces_dir: Optional[str] = None,
        metrics_dir: Optional[str] = None,
        base_dir: Optional[str] = None,
        analysis_mode: str = 'latest',
        example_name: Optional[str] = None,
        tags: Optional[Iterable[str]] = None,
    ):
        """Initialize the analyzer.

        Args:
            trace_file: Path to trace JSONL file (if None, uses traces_dir based on analysis_mode)
            metrics_file: Path to metrics JSONL file (if None, uses metrics_dir based on analysis_mode)
            traces_dir: Directory containing trace files (default: "traces" in current directory)
            metrics_dir: Directory containing metrics files (default: "metrics" in current directory)
            base_dir: Base directory for traces_dir and metrics_dir (default: current working directory)
            analysis_mode: Analysis mode - one of:
                - 'latest': Use only the latest trace/metrics file (default, for quick analysis)
                - 'per_run': Analyze each run separately, then compute statistics across runs (✅ recommended for academic papers)
        """
        # Use current working directory as base if not specified
        if base_dir is None:
            base_dir = str(Path.cwd())

        # Set default directories relative to base_dir
        if traces_dir is None:
            traces_dir = str(Path(base_dir) / "traces")
        else:
            traces_dir = str(Path(base_dir) / traces_dir) if not Path(traces_dir).is_absolute() else traces_dir

        if metrics_dir is None:
            metrics_dir = str(Path(base_dir) / "metrics")
        else:
            metrics_dir = str(Path(base_dir) / metrics_dir) if not Path(metrics_dir).is_absolute() else metrics_dir

        self.traces_dir = traces_dir
        self.metrics_dir = metrics_dir
        self.analysis_mode = analysis_mode
        self.example_name = example_name
        self.tags = list(tags) if tags is not None else None
        self._traces_parquet = Path(traces_dir).suffix.lower() == ".parquet"
        self._metrics_parquet = Path(metrics_dir).suffix.lower() == ".parquet"

        if trace_file:
            self.trace_files = [trace_file]
        elif self._traces_parquet:
            self.trace_files = [traces_dir]
        else:
            # Collect all trace files (JSONL or JSON)
            all_trace_files_raw = (
                glob.glob(f"{traces_dir}/*.jsonl")
                + glob.glob(f"{traces_dir}/*.json")
            )
            # Sort by timestamp in filename if available, otherwise by mtime
            # This ensures consistent ordering even when mtime is identical
            def sort_key(p):
                filename = Path(p).stem
                match = re.search(r'(\d{8}_\d{6})', filename)
                if match:
                    # Use timestamp for sorting (larger timestamp = newer)
                    return (1, match.group(1))  # 1 = has timestamp, sort by timestamp
                else:
                    # Fallback to mtime for files without timestamp
                    return (0, Path(p).stat().st_mtime)

            all_trace_files = sorted(all_trace_files_raw, key=sort_key, reverse=True)
            if not all_trace_files:
                raise ValueError(
                    f"No trace files found in {traces_dir}/ (expected .jsonl or .json files)"
                )

            if analysis_mode == 'latest':
                self.trace_files = [all_trace_files[0]]  # Use latest only
            elif analysis_mode == 'per_run':
                self.trace_files = all_trace_files  # Analyze each separately
            else:
                raise ValueError(f"Invalid analysis_mode: {analysis_mode}. Must be 'latest' or 'per_run'")

        if metrics_file:
            self.metrics_files = [metrics_file]
        elif self._metrics_parquet:
            self.metrics_files = [metrics_dir]
        else:
            all_metrics_files = sorted(glob.glob(f"{metrics_dir}/*.jsonl"),
                                     key=lambda p: Path(p).stat().st_mtime, reverse=True)
            if analysis_mode == 'latest':
                self.metrics_files = [all_metrics_files[0]] if all_metrics_files else []
            elif analysis_mode == 'per_run':
                self.metrics_files = all_metrics_files  # Analyze each separately
            else:
                self.metrics_files = []

        # For backward compatibility
        self.trace_file = self.trace_files[0] if self.trace_files else None
        self.metrics_file = self.metrics_files[0] if self.metrics_files else None

        self.traces: List[Dict] = []
        self.metrics: List[Dict] = []
        self._loaded = False

    def load_data(self):
        """Load trace and metrics data."""
        if self._traces_parquet or self._metrics_parquet:
            from collections import defaultdict

            traces = []
            metrics = []
            if self.trace_files:
                traces = load_traces(
                    self.trace_files[0],
                    example_name=self.example_name,
                    tags=self.tags,
                )
            if self.metrics_files:
                metrics = load_metrics(
                    self.metrics_files[0],
                    example_name=self.example_name,
                    tags=self.tags,
                )

            if self.analysis_mode == "per_run":
                if traces and not any(span.get("run_id") for span in traces):
                    print("Warning: run_id missing in traces; per_run analysis will treat all rows as one run.")
                traces_by_run = defaultdict(list)
                metrics_by_run = defaultdict(list)
                for span in traces:
                    run_id = span.get("run_id") or ""
                    traces_by_run[run_id].append(span)
                for metric in metrics:
                    run_id = metric.get("run_id") or ""
                    metrics_by_run[run_id].append(metric)

                run_ids = sorted(traces_by_run.keys(), reverse=True)
                self.run_traces = [(run_id, traces_by_run[run_id]) for run_id in run_ids]
                self.run_metrics = [(run_id, metrics_by_run.get(run_id, [])) for run_id in run_ids]
                if self.run_traces:
                    self.traces = self.run_traces[0][1]
                if self.run_metrics:
                    self.metrics = self.run_metrics[0][1]
            else:
                if traces and any(span.get("run_id") for span in traces):
                    traces_by_run = defaultdict(list)
                    for span in traces:
                        run_id = span.get("run_id") or ""
                        traces_by_run[run_id].append(span)
                    latest_run = sorted(traces_by_run.keys(), reverse=True)[0]
                    self.traces = traces_by_run[latest_run]
                else:
                    self.traces = traces

                if metrics and any(metric.get("run_id") for metric in metrics):
                    metrics_by_run = defaultdict(list)
                    for metric in metrics:
                        run_id = metric.get("run_id") or ""
                        metrics_by_run[run_id].append(metric)
                    latest_run = sorted(metrics_by_run.keys(), reverse=True)[0]
                    self.metrics = metrics_by_run[latest_run]
                else:
                    self.metrics = metrics

            self._loaded = True
            return

        if self.analysis_mode == 'per_run':
            # For per-run analysis, we need to handle two cases:
            # 1. Monolithic system (marketing-agency): one trace file = one run
            # 2. Distributed system (dist_vs_monolithic_without_kagent): multiple trace files per run (one per agent)

            # Group trace files by timestamp (extracted from filename or file modification time)
            # Pattern: agent-name_YYYYMMDD_HHMMSS.(jsonl|json)
            from collections import defaultdict
            import re

            # Group by timestamp (extracted from filename)
            runs_by_timestamp = defaultdict(list)
            for trace_file in self.trace_files:
                filename = Path(trace_file).stem
                # Try to extract timestamp pattern YYYYMMDD_HHMMSS
                match = re.search(r'(\d{8}_\d{6})', filename)
                if match:
                    timestamp = match.group(1)
                    runs_by_timestamp[timestamp].append(trace_file)
                else:
                    # If no timestamp found, use file modification time (rounded to minute) as key
                    # This handles cases where filename doesn't have timestamp
                    mtime = Path(trace_file).stat().st_mtime
                    timestamp = str(int(mtime // 60))  # Round to minute
                    runs_by_timestamp[timestamp].append(trace_file)

            # If we have multiple files per timestamp, it's a distributed system
            # Otherwise, it's a monolithic system (one file per run)
            is_distributed = any(len(files) > 1 for files in runs_by_timestamp.values())

            # For distributed systems, files with the same timestamp belong to the same run
            # No need to merge by time window - unified timestamp ensures accuracy
            # (The unified timestamp is set via OTEL_RUN_TIMESTAMP environment variable at startup)

            if is_distributed:
                # Distributed system: merge files with same/similar timestamp into one run
                print(f"Detected distributed system: grouping {len(self.trace_files)} trace files into runs by timestamp...")
                self.trace_files_grouped = []
                for timestamp, files in sorted(runs_by_timestamp.items()):
                    print(f"  Run {len(self.trace_files_grouped) + 1} (timestamp: {timestamp}): {len(files)} files")
                    self.trace_files_grouped.append(files)
            else:
                # Monolithic system: one file per run (marketing-agency case)
                print(f"Detected monolithic system: {len(self.trace_files)} trace files (one per run)...")
                self.trace_files_grouped = [[f] for f in self.trace_files]

            # Group metrics files similarly
            if self.metrics_files:
                metrics_by_timestamp = defaultdict(list)
                for metrics_file in self.metrics_files:
                    filename = Path(metrics_file).stem
                    match = re.search(r'(\d{8}_\d{6})', filename)
                    if match:
                        timestamp = match.group(1)
                        metrics_by_timestamp[timestamp].append(metrics_file)
                    else:
                        mtime = Path(metrics_file).stat().st_mtime
                        timestamp = str(int(mtime // 60))
                        metrics_by_timestamp[timestamp].append(metrics_file)

                # For distributed systems, files with the same timestamp belong to the same run
                # No need to merge by time window - unified timestamp ensures accuracy
                # (The unified timestamp is set via OTEL_RUN_TIMESTAMP environment variable)

                if is_distributed:
                    self.metrics_files_grouped = []
                    # Use the same order as trace_files_grouped to ensure alignment
                    for timestamp, _ in sorted(runs_by_timestamp.items()):
                        # Find matching metrics files with the exact same timestamp
                        # Unified timestamp ensures all files from the same run have identical timestamps
                        matching_metrics = metrics_by_timestamp.get(timestamp, [])
                        self.metrics_files_grouped.append(matching_metrics)
                else:
                    # Monolithic system: match metrics files to trace files by timestamp
                    # This handles cases where trace and metrics files have different naming conventions
                    # but share the same timestamp (e.g., financial_analyzer_traces-20251211_123420.json
                    # and system_metrics_20251211_123420.jsonl)
                    self.metrics_files_grouped = []
                    for trace_file in self.trace_files:
                        trace_filename = Path(trace_file).stem
                        # Extract timestamp from trace filename
                        trace_match = re.search(r'(\d{8}_\d{6})', trace_filename)
                        if trace_match:
                            trace_timestamp = trace_match.group(1)
                            # Find matching metrics file by timestamp
                            matching_metrics = []
                            for metrics_file in self.metrics_files:
                                metrics_filename = Path(metrics_file).stem
                                metrics_match = re.search(r'(\d{8}_\d{6})', metrics_filename)
                                if metrics_match and metrics_match.group(1) == trace_timestamp:
                                    matching_metrics.append(metrics_file)
                            self.metrics_files_grouped.append(matching_metrics if matching_metrics else [])
                        else:
                            # Fallback: try to match by stem if no timestamp found
                            trace_stem = trace_filename
                            matching_metrics = [m for m in self.metrics_files if Path(m).stem == trace_stem]
                            self.metrics_files_grouped.append(matching_metrics if matching_metrics else [])
            else:
                self.metrics_files_grouped = [[]] * len(self.trace_files_grouped)

            # Load and merge files per run
            self.run_traces = []
            self.run_metrics = []

            print(f"\nLoading {len(self.trace_files_grouped)} runs for per-run analysis...")
            for run_idx, (trace_files_group, metrics_files_group) in enumerate(zip(self.trace_files_grouped, self.metrics_files_grouped)):
                # Merge all trace files for this run
                merged_traces = []
                for trace_file in trace_files_group:
                    traces = load_traces(trace_file)
                    merged_traces.extend(traces)

                # Merge all metrics files for this run
                merged_metrics = []
                for metrics_file in metrics_files_group:
                    metrics = load_metrics(metrics_file)
                    merged_metrics.extend(metrics)

                # Use first trace file name as identifier
                run_id = Path(trace_files_group[0]).stem if trace_files_group else f"run_{run_idx+1}"
                self.run_traces.append((run_id, merged_traces))
                self.run_metrics.append((run_id, merged_metrics))

                print(f"  Run {run_idx + 1}: {len(merged_traces)} spans, {len(merged_metrics)} metrics")

            total_spans = sum(len(traces) for _, traces in self.run_traces)
            total_metrics = sum(len(metrics) for _, metrics in self.run_metrics)
            print(f"Loaded {total_spans} total trace spans across {len(self.run_traces)} run(s)")
            if total_metrics > 0:
                print(f"Loaded {total_metrics} total metric records across {len(self.run_metrics)} run(s)")
            else:
                print("Warning: No metrics data found")

            # For backward compatibility, also set merged data
            self.traces = []
            for _, traces in self.run_traces:
                self.traces.extend(traces)
            self.metrics = []
            for _, metrics in self.run_metrics:
                self.metrics.extend(metrics)
        else:
            # Latest mode - should only have one file
            if len(self.trace_files) != 1:
                raise ValueError(f"Expected 1 trace file in 'latest' mode, got {len(self.trace_files)}")

            print(f"Loading trace file: {self.trace_files[0]}")
            self.traces = load_traces(self.trace_files[0])
            print(f"Loaded {len(self.traces)} trace spans")

            # Load metrics file
            self.metrics = []
            if self.metrics_files:
                if len(self.metrics_files) != 1:
                    raise ValueError(f"Expected 1 metrics file in 'latest' mode, got {len(self.metrics_files)}")
                print(f"Loading metrics file: {self.metrics_files[0]}")
                self.metrics = load_metrics(self.metrics_files[0])
                print(f"Loaded {len(self.metrics)} metric records")
            else:
                print("Warning: No metrics file found")

        self._loaded = True

    def analyze(self, output_dir: str = "visualizations"):
        """Run complete analysis and generate all visualizations.

        Args:
            output_dir: Directory to save visualization files
        """
        if not self._loaded:
            self.load_data()

        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)

        print("\nGenerating visualizations...")
        print()

        if self.analysis_mode == 'per_run':
            # Per-run analysis mode (recommended for academic papers)
            print("=" * 80)
            print("Per-Run Analysis Mode (Recommended for Academic Papers)")
            print("=" * 80)
            print()

            # Analyze each run separately and compute statistics
            # For distributed systems, we've already grouped files by run in load_data()
            # Extract the merged trace/metrics data per run
            run_trace_data = []
            run_metrics_data = []
            # Match traces and metrics by run_id (filename)
            # This ensures traces and metrics are from the same run based on filename
            run_metrics_dict = dict(self.run_metrics)
            for run_id, traces in self.run_traces:
                if not traces:
                    continue
                # Match by run_id (filename)
                matching_metrics = run_metrics_dict.get(run_id)
                # Debug: Verify matching
                if matching_metrics and len(matching_metrics) > 0:
                    first_metric = matching_metrics[0]
                    if first_metric.get('data_points'):
                        first_dp = first_metric['data_points'][0]
                        first_metric_ts = first_dp.get('timestamp', 0) / 1e9
                        first_trace_start = traces[0].get('start_time', 0)
                        if isinstance(first_trace_start, (int, float)):
                            first_trace_start_sec = first_trace_start / 1e9 if first_trace_start > 1e12 else first_trace_start
                            time_diff = abs(first_trace_start_sec - first_metric_ts)
                            if time_diff > 60:
                                print(f"   ⚠️  Warning: Run {run_id} has large time difference: {time_diff:.2f} seconds")
                                print(f"      Trace start: {first_trace_start_sec:.2f}, Metric start: {first_metric_ts:.2f}")
                run_trace_data.append(traces)
                run_metrics_data.append(matching_metrics if matching_metrics else None)

            # Use the new method that works with pre-loaded and grouped data
            results = self._analyze_per_run_from_data(run_trace_data, run_metrics_data)

            # Generate per-run visualizations with statistics
            plot_per_run_statistics(results, str(output_path))
        else:
            # Standard analysis mode (latest or merged)
            self._generate_standard_visualizations(
                traces=self.traces,
                metrics=self.metrics if self.metrics else None,
                extracted_data=None,  # Will extract on the fly
                output_path=output_path,
                suffix=""
            )

        print()
        print("=" * 80)
        print("All visualizations generated successfully!")
        print(f"Output directory: {output_path.absolute()}")
        print("=" * 80)

    def _generate_standard_visualizations(self, traces: List[Dict], metrics: Optional[List[Dict]],
                                          extracted_data: Optional[Dict], output_path: Path, suffix: str = ""):
        """Generate standard visualizations for a single run.

        Args:
            traces: List of trace spans
            metrics: List of metric records (can be None)
            extracted_data: Pre-extracted data dict (if None, will extract on the fly)
            output_path: Directory to save visualizations
            suffix: Suffix to add to output filenames (e.g., "_latest")
        """
        # Extract data if not provided
        if extracted_data is None:
            # 1. Token Consumption
            print("1. Token Consumption...")
            total_tokens, per_agent_tokens = extract_token_consumption(traces)
            plot_token_consumption(total_tokens, per_agent_tokens,
                                  str(output_path / f"1_token_consumption{suffix}.pdf"))

            # 2. Delay Breakdown
            print("2. Delay Breakdown...")
            delays, per_agent_delays, inter_agent_delays, agent_llm_delays = extract_delay_breakdown(traces)
            plot_delay_breakdown(delays, per_agent_delays, inter_agent_delays, agent_llm_delays,
                                str(output_path / f"2_delay_breakdown{suffix}.pdf"))

            # 3. CPU/Memory Usage
            if metrics:
                print("3. CPU/Memory Usage...")
                usage = extract_cpu_memory_usage(metrics)
                plot_cpu_memory_usage(usage, str(output_path / f"3_cpu_memory_usage{suffix}.pdf"))
            else:
                print("3. CPU/Memory Usage... (skipped - no metrics data)")

            # 4. Message Sizes
            print("4. Message Sizes...")
            sizes, per_agent_sizes, inter_agent_sizes, agent_llm_sizes = extract_message_sizes(traces)
            plot_message_sizes(sizes, per_agent_sizes, inter_agent_sizes, agent_llm_sizes,
                              str(output_path / f"4_message_sizes{suffix}.pdf"))

            # 5. Events/Spans with CPU/Memory
            print("5. Events/Spans with CPU/Memory...")
            if metrics:
                usage = extract_cpu_memory_usage(metrics)
                plot_events_with_cpu_memory(traces, usage,
                                          str(output_path / f"6_events_with_cpu_memory{suffix}.pdf"))
            else:
                # Plot spans even without CPU/memory data
                empty_usage = {'cpu': {'process': []}, 'memory': {'process': []}}
                plot_events_with_cpu_memory(traces, empty_usage,
                                          str(output_path / f"6_events_with_cpu_memory{suffix}.pdf"))

            # 6. Latency Flame Graph (span-based aggregation)
            print(f"6. Latency Flame Graph{suffix.replace('_', ' ')} (aggregated spans)...")
            plot_latency_flame(
                traces,
                str(output_path / f"7_latency_flame{suffix}.pdf"),
                title="Request Latency Flame Graph (Aggregated Spans)"
            )

            # 6. Latency Flame Graph (span-based aggregation)
            print("6. Latency Flame Graph (aggregated spans)...")
            plot_latency_flame(
                traces,
                str(output_path / f"7_latency_flame{suffix}.pdf"),
                title="Request Latency Flame Graph (Aggregated Spans)"
            )
        else:
            # Use pre-extracted data (avoids duplicate calculation)
            # 1. Token Consumption
            print(f"1. Token Consumption{suffix.replace('_', ' ')}...")
            plot_token_consumption(
                extracted_data['tokens']['total'],
                extracted_data['tokens']['per_agent'],
                str(output_path / f"1_token_consumption{suffix}.pdf")
            )

            # 2. Delay Breakdown
            print(f"2. Delay Breakdown{suffix.replace('_', ' ')}...")
            plot_delay_breakdown(
                extracted_data['delays']['component'],
                extracted_data['delays']['per_agent'],
                extracted_data['delays']['inter_agent'],
                extracted_data['delays']['agent_llm'],
                str(output_path / f"2_delay_breakdown{suffix}.pdf")
            )

            # 3. CPU/Memory Usage
            if extracted_data['resource_usage']:
                print(f"3. CPU/Memory Usage{suffix.replace('_', ' ')}...")
                plot_cpu_memory_usage(
                    extracted_data['resource_usage'],
                    str(output_path / f"3_cpu_memory_usage{suffix}.pdf")
                )
            else:
                print(f"3. CPU/Memory Usage{suffix.replace('_', ' ')}... (skipped - no metrics data)")

            # 4. Message Sizes
            print(f"4. Message Sizes{suffix.replace('_', ' ')}...")
            plot_message_sizes(
                extracted_data['message_sizes']['component'],
                extracted_data['message_sizes']['per_agent'],
                extracted_data['message_sizes']['inter_agent'],
                extracted_data['message_sizes']['agent_llm'],
                str(output_path / f"4_message_sizes{suffix}.pdf")
            )

            # 5. Events/Spans with CPU/Memory
            print(f"5. Events/Spans with CPU/Memory{suffix.replace('_', ' ')}...")
            # Always use metrics if available to ensure traces and resource_usage are from the same run
            # This is critical for time alignment
            usage = None

            # First, try to use metrics directly if available
            # This ensures traces and metrics are from the same run
            if metrics and len(metrics) > 0:
                usage = extract_cpu_memory_usage(metrics)
                # Verify alignment: check if first trace and first CPU time are close
                if traces and usage.get('cpu', {}).get('process'):
                    first_trace_start = traces[0].get('start_time', 0)
                    if isinstance(first_trace_start, (int, float)):
                        first_trace_start_sec = first_trace_start / 1e9 if first_trace_start > 1e12 else first_trace_start
                        first_cpu_time = usage['cpu']['process'][0][0]
                        time_diff = abs(first_trace_start_sec - first_cpu_time)
                        if time_diff > 60:  # More than 60 seconds difference
                            print(f"   ⚠️  Warning: Large time difference between traces and metrics: {time_diff:.2f} seconds")
                            print(f"      This suggests traces and metrics may be from different runs!")
            # If metrics not available, try extracted_data['resource_usage']
            elif extracted_data.get('resource_usage'):
                resource_usage = extracted_data['resource_usage']
                # Check if resource_usage has actual data
                cpu_data = resource_usage.get('cpu', {}).get('process', [])
                mem_data = resource_usage.get('memory', {}).get('process', [])
                if cpu_data or mem_data:
                    usage = resource_usage
                else:
                    # resource_usage exists but is empty, this shouldn't happen but handle gracefully
                    print(f"   Warning: resource_usage exists but is empty (CPU: {len(cpu_data)}, Memory: {len(mem_data)})")
                    usage = None

            if usage:
                plot_events_with_cpu_memory(traces, usage,
                                          str(output_path / f"6_events_with_cpu_memory{suffix}.pdf"))
            else:
                # Plot spans even without CPU/memory data
                empty_usage = {'cpu': {'process': []}, 'memory': {'process': []}}
                plot_events_with_cpu_memory(traces, empty_usage,
                                          str(output_path / f"6_events_with_cpu_memory{suffix}.pdf"))

    def _analyze_per_run_from_data(self, run_trace_data: List[List[Dict]], run_metrics_data: List[Optional[List[Dict]]]) -> Dict:
        """Analyze per-run data that's already loaded and grouped.

        This is a wrapper around analyze_per_run that works with pre-loaded data
        instead of file paths. This is needed for distributed systems where
        multiple trace files need to be merged per run.

        Args:
            run_trace_data: List of trace data lists, one per run
            run_metrics_data: List of metrics data lists, one per run (can be None)

        Returns:
            Same format as analyze_per_run
        """
        from .per_run_analyzer import compute_cross_run_statistics
        from .extractors import (
            extract_call_graph,
            extract_call_sequence,
        )

        results = {
            'runs': [],
            'statistics': {}
        }

        # Analyze each run
        for i, (traces, metrics) in enumerate(zip(run_trace_data, run_metrics_data)):
            # Extract metrics for this run
            run_data = {
                'run_id': i + 1,
                'trace_file': f"run_{i+1}",
                'traces': traces,
                'metrics': metrics if metrics else [],
            }

            # Extract token consumption
            total_tokens, per_agent_tokens = extract_token_consumption(traces)
            run_data['tokens'] = {
                'total': total_tokens,
                'per_agent': per_agent_tokens
            }

            # Extract delay breakdown
            delays, per_agent_delays, inter_agent_delays, agent_llm_delays = extract_delay_breakdown(traces)
            run_data['delays'] = {
                'component': delays,
                'per_agent': per_agent_delays,
                'inter_agent': inter_agent_delays,
                'agent_llm': agent_llm_delays
            }

            # Extract message sizes
            sizes, per_agent_sizes, inter_agent_sizes, agent_llm_sizes = extract_message_sizes(traces)
            run_data['message_sizes'] = {
                'component': sizes,
                'per_agent': per_agent_sizes,
                'inter_agent': inter_agent_sizes,
                'agent_llm': agent_llm_sizes
            }

            # Extract CPU/memory usage
            if metrics and len(metrics) > 0:
                usage = extract_cpu_memory_usage(metrics)
                run_data['resource_usage'] = usage
            else:
                run_data['resource_usage'] = None

            # Extract call graph edges (set, unordered)
            call_graph_edges = extract_call_graph(traces)
            run_data['call_graph'] = call_graph_edges

            # Extract call sequence (ordered list, preserves calling order)
            call_sequence = extract_call_sequence(traces)
            run_data['call_sequence'] = call_sequence

            results['runs'].append(run_data)

        # Compute statistics across runs
        results['statistics'] = compute_cross_run_statistics(results['runs'])

        return results
