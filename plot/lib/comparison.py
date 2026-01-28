"""
Cross-example comparison analysis module.
Supports extracting metrics from multiple examples and generating comparison visualizations.
"""

from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path
from collections import defaultdict, Counter
import numpy as np
import matplotlib.pyplot as plt

from .analyzer import MetricsAnalyzer
from .extractors import (
    extract_token_consumption,
    extract_delay_breakdown,
    extract_message_sizes,
    extract_cpu_memory_usage,
    extract_call_graph,
    extract_call_sequence,
)


class ExampleMetrics:
    """Metrics data container for a single example."""

    def __init__(self, example_name: str):
        self.example_name = example_name
        self.tokens = None
        self.delays = None
        self.message_sizes = None
        self.resource_usage = None
        self.call_graph = None  # Set of (source, target) tuples (for latest mode)
        self.call_graphs_per_run = []  # List of call graphs (sets), one per run (for per_run mode)
        self.call_sequences_per_run = []  # List of call sequences (ordered lists), one per run (for per_run mode)
        self.average_pairwise_normalized_ged = None  # Average pairwise normalized graph edit distance across all runs within this example
        self.average_pairwise_lcs = None  # Average pairwise LCS similarity across all runs within this example
        self.average_pairwise_ged_std = None  # Std dev of pairwise GED values
        self.average_pairwise_lcs_std = None  # Std dev of pairwise LCS values
        self.average_pairwise_jaccard = None  # Average pairwise Jaccard similarity across runs (edge sets, unweighted)
        self.average_pairwise_jaccard_std = None  # Std dev of pairwise Jaccard similarities
        self.pairwise_normalized_ged = []  # All pairwise normalized GED values
        self.pairwise_lcs = []  # All pairwise LCS similarity values
        self.pairwise_jaccard = []  # All pairwise Jaccard similarity values
        self.traces = []
        self.metrics = []

    def extract_all_metrics(self, traces: List[Dict], metrics: Optional[List[Dict]] = None):
        """Extract all metrics from traces and metrics (latest mode)."""
        # Token consumption
        total_tokens, per_agent_tokens = extract_token_consumption(traces)
        self.tokens = {
            'total': total_tokens,
            'per_agent': per_agent_tokens
        }

        # Delay breakdown
        delays, per_agent_delays, inter_agent_delays, agent_llm_delays = extract_delay_breakdown(traces)
        self.delays = {
            'component': delays,
            'per_agent': per_agent_delays,
            'inter_agent': inter_agent_delays,
            'agent_llm': agent_llm_delays
        }

        # Message sizes
        sizes, per_agent_sizes, inter_agent_sizes, agent_llm_sizes = extract_message_sizes(traces)
        self.message_sizes = {
            'component': sizes,
            'per_agent': per_agent_sizes,
            'inter_agent': inter_agent_sizes,
            'agent_llm': agent_llm_sizes
        }

        # CPU/Memory usage
        if metrics:
            self.resource_usage = extract_cpu_memory_usage(metrics)
        else:
            self.resource_usage = None

        # Call graph
        self.call_graph = extract_call_graph(traces)

        self.traces = traces
        self.metrics = metrics or []

    def extract_call_graphs_from_runs(
        self,
        run_traces: List[Tuple[str, List[Dict]]],
        *,
        compute_ged: bool = True,
    ):
        """Extract weighted directed call graphs from multiple runs and compute similarities.

        Args:
            run_traces: List of (run_id, traces) tuples
            compute_ged: Whether to compute normalized GED (can be expensive)
        """
        # Extract weighted call graph (edge -> call count) for each run
        self.call_graphs_per_run = []
        self.call_sequences_per_run = []
        for run_id, traces in run_traces:
            call_sequence = extract_call_sequence(traces)
            call_graph = Counter(call_sequence)
            self.call_graphs_per_run.append(call_graph)
            self.call_sequences_per_run.append(call_sequence)
            # Debug: print call graph info
            graph_size = compute_graph_size(call_graph)
            nodes = set()
            for source, target in call_graph:
                nodes.add(source)
                nodes.add(target)
            total_calls = sum(call_graph.values())
            print(f"  Run {run_id}: {len(nodes)} nodes, {len(call_graph)} edges (graph), {total_calls} calls (weight sum), size={graph_size}")
            if len(call_graph) > 0:
                edges_preview = sorted(call_graph.items())
                print(
                    f"    Graph edges: {edges_preview[:5]}..."
                    if len(edges_preview) > 5
                    else f"    Graph edges: {edges_preview}"
                )

        # Compute average pairwise normalized GED across all runs within this example
        if compute_ged:
            if len(self.call_graphs_per_run) >= 2:
                # Compute all pairwise normalized GED values
                n = len(self.call_graphs_per_run)
                pairwise_distances = []
                for i in range(n):
                    for j in range(i + 1, n):
                        normalized_ged = compute_normalized_sequence_edit_distance(
                            self.call_sequences_per_run[i],
                            self.call_sequences_per_run[j]
                        )
                        pairwise_distances.append(normalized_ged)
                        print(f"  Pair ({i+1}, {j+1}): graph_ged={normalized_ged:.4f}")
                self.pairwise_normalized_ged = pairwise_distances

                # Average = mean of all pairwise distances
                # Total number of pairs: n*(n-1)/2
                self.average_pairwise_normalized_ged = np.mean(pairwise_distances) if pairwise_distances else 0.0
                self.average_pairwise_ged_std = np.std(pairwise_distances) if pairwise_distances else 0.0
                print(f"  Average pairwise normalized GED: {self.average_pairwise_normalized_ged:.4f}")
            elif len(self.call_graphs_per_run) == 1:
                # Only one run, cannot compute distance
                self.average_pairwise_normalized_ged = 0.0
                self.average_pairwise_ged_std = 0.0
                self.pairwise_normalized_ged = [0.0]
            else:
                self.average_pairwise_normalized_ged = None
                self.average_pairwise_ged_std = None
                self.pairwise_normalized_ged = []
        else:
            self.average_pairwise_normalized_ged = 0.0
            self.average_pairwise_ged_std = 0.0
            self.pairwise_normalized_ged = []

        if len(self.call_sequences_per_run) >= 2:
            pairwise_lcs = []
            n = len(self.call_sequences_per_run)
            for i in range(n):
                for j in range(i + 1, n):
                    similarity = compute_lcs_similarity(
                        self.call_sequences_per_run[i],
                        self.call_sequences_per_run[j]
                    )
                    pairwise_lcs.append(similarity)
            self.pairwise_lcs = pairwise_lcs
            self.average_pairwise_lcs = np.mean(pairwise_lcs) if pairwise_lcs else 0.0
            self.average_pairwise_lcs_std = np.std(pairwise_lcs) if pairwise_lcs else 0.0
        elif len(self.call_sequences_per_run) == 1:
            self.average_pairwise_lcs = 0.0
            self.average_pairwise_lcs_std = 0.0
            self.pairwise_lcs = [0.0]
        else:
            self.average_pairwise_lcs = None
            self.average_pairwise_lcs_std = None
            self.pairwise_lcs = []

        # Jaccard similarity on edge sets (ignoring weights)
        if len(self.call_graphs_per_run) >= 2:
            pairwise_jaccard = []
            n = len(self.call_graphs_per_run)
            for i in range(n):
                edges_i = set(self.call_graphs_per_run[i].keys())
                for j in range(i + 1, n):
                    edges_j = set(self.call_graphs_per_run[j].keys())
                    union = edges_i | edges_j
                    inter = edges_i & edges_j
                    similarity = len(inter) / len(union) if union else 0.0
                    pairwise_jaccard.append(similarity)
            self.pairwise_jaccard = pairwise_jaccard
            self.average_pairwise_jaccard = np.mean(pairwise_jaccard) if pairwise_jaccard else 0.0
            self.average_pairwise_jaccard_std = np.std(pairwise_jaccard) if pairwise_jaccard else 0.0
        elif len(self.call_graphs_per_run) == 1:
            self.average_pairwise_jaccard = 0.0
            self.average_pairwise_jaccard_std = 0.0
            self.pairwise_jaccard = [0.0]
        else:
            self.average_pairwise_jaccard = None
            self.average_pairwise_jaccard_std = None
            self.pairwise_jaccard = []


class MultiExampleCollector:
    """Multi-example metrics collector."""

    def __init__(self):
        self.examples: Dict[str, ExampleMetrics] = {}

    def add_example(
        self,
        example_name: str,
        traces_dir: str,
        metrics_dir: Optional[str] = None,
        base_dir: Optional[str] = None,
        analysis_mode: str = 'latest',
        compute_ged: bool = True,
        dataset_example_name: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ExampleMetrics:
        """Add an example and extract its metrics.

        Args:
            example_name: Example name
            traces_dir: Path to traces directory
            metrics_dir: Path to metrics directory (optional)
            base_dir: Base directory (optional)
            analysis_mode: Analysis mode ('latest' or 'per_run')

        Returns:
            ExampleMetrics object
        """
        # Use existing MetricsAnalyzer to load data
        analyzer = MetricsAnalyzer(
            traces_dir=traces_dir,
            metrics_dir=metrics_dir,
            base_dir=base_dir,
            analysis_mode=analysis_mode,
            example_name=dataset_example_name or example_name,
            tags=tags,
        )
        analyzer.load_data()

        # Create metrics object and extract data
        example_metrics = ExampleMetrics(example_name)

        if analysis_mode == 'per_run' and hasattr(analyzer, 'run_traces'):
            # per_run mode: extract call graph for each run and compute average pairwise normalized GED
            example_metrics.extract_call_graphs_from_runs(
                analyzer.run_traces,
                compute_ged=compute_ged,
            )
            # Also extract metrics from latest run for other comparisons
            if analyzer.run_traces:
                latest_run_id, latest_traces = analyzer.run_traces[0]  # First one is the latest
                latest_metrics = analyzer.run_metrics[0][1] if analyzer.run_metrics else None
                example_metrics.extract_all_metrics(latest_traces, latest_metrics)
        else:
            # latest mode: extract only one call graph
            example_metrics.extract_all_metrics(analyzer.traces, analyzer.metrics)

        self.examples[example_name] = example_metrics
        return example_metrics

    def get_metric(self, example_name: str, metric_type: str):
        """Get specified metric for specified example.

        Args:
            example_name: Example name
            metric_type: Metric type ('tokens', 'delays', 'message_sizes', 'resource_usage', 'call_graph')

        Returns:
            Metric data
        """
        if example_name not in self.examples:
            raise ValueError(f"Example '{example_name}' not found")

        example = self.examples[example_name]
        return getattr(example, metric_type, None)

    def get_all_examples(self) -> Dict[str, ExampleMetrics]:
        """Get all examples."""
        return self.examples


def compute_graph_size(edges: Set[Tuple[str, str]] | Dict[Tuple[str, str], int]) -> int:
    """Compute graph size (number of nodes).

    Args:
        edges: Edge set or weighted edge map (source, target) -> weight

    Returns:
        Graph size = number of nodes
    """
    if not edges:
        return 0

    # Collect all nodes
    nodes = set()
    for source, target in edges:
        nodes.add(source)
        nodes.add(target)

    return len(nodes)


def compute_lcs_similarity(seq1: List[Tuple[str, str]], seq2: List[Tuple[str, str]]) -> float:
    if not seq1 and not seq2:
        return 1.0
    if not seq1 or not seq2:
        return 0.0

    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs_len = dp[m][n]
    return lcs_len / max(m, n) if max(m, n) > 0 else 0.0


def compute_sequence_edit_distance(seq1: List[Tuple[str, str]], seq2: List[Tuple[str, str]]) -> int:
    """Compute Levenshtein edit distance between two call sequences.

    Costs: insert = 1, delete = 1, substitute = 1.
    """
    if not seq1:
        return len(seq2)
    if not seq2:
        return len(seq1)

    m, n = len(seq1), len(seq2)
    prev = list(range(n + 1))
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        curr[0] = i
        s1 = seq1[i - 1]
        for j in range(1, n + 1):
            cost = 0 if s1 == seq2[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev, curr = curr, prev
    return prev[n]


def compute_normalized_sequence_edit_distance(
    seq1: List[Tuple[str, str]],
    seq2: List[Tuple[str, str]],
) -> float:
    """Compute normalized sequence edit distance.

    Normalization = edit distance / max(sequence length).
    """
    max_len = max(len(seq1), len(seq2))
    if max_len == 0:
        return 0.0
    return compute_sequence_edit_distance(seq1, seq2) / max_len


def _to_weight_map(graph: Set[Tuple[str, str]] | Dict[Tuple[str, str], int]) -> Dict[Tuple[str, str], int]:
    if isinstance(graph, dict):
        return {edge: int(weight) for edge, weight in graph.items()}
    return {edge: 1 for edge in graph}


def compute_graph_edit_distance(
    graph1: Set[Tuple[str, str]] | Dict[Tuple[str, str], int],
    graph2: Set[Tuple[str, str]] | Dict[Tuple[str, str], int]
) -> int:
    """Compute weighted graph edit distance between two call graphs.

    Graph edit distance = edge weight L1 distance + node insert/delete cost.

    Args:
        graph1: Edge set or weighted edge map for the first graph
        graph2: Edge set or weighted edge map for the second graph

    Returns:
        Graph edit distance (integer)
    """
    weights1 = _to_weight_map(graph1)
    weights2 = _to_weight_map(graph2)
    all_edges = set(weights1) | set(weights2)
    edge_distance = sum(abs(weights1.get(edge, 0) - weights2.get(edge, 0)) for edge in all_edges)

    nodes1 = {node for edge in weights1 for node in edge}
    nodes2 = {node for edge in weights2 for node in edge}
    node_distance = len(nodes1 ^ nodes2)

    return edge_distance + node_distance


def compute_normalized_graph_edit_distance(
    graph1: Set[Tuple[str, str]] | Dict[Tuple[str, str], int],
    graph2: Set[Tuple[str, str]] | Dict[Tuple[str, str], int]
) -> float:
    """Compute normalized graph edit distance.

    Normalization = graph edit distance / total_call_weight

    Args:
        graph1: Edge set of the first graph
        graph2: Edge set of the second graph

    Returns:
        Normalized graph edit distance (0 means identical)
    """
    edit_distance = compute_graph_edit_distance(graph1, graph2)

    weights1 = _to_weight_map(graph1)
    weights2 = _to_weight_map(graph2)
    total_edge_weight = sum(weights1.values()) + sum(weights2.values())

    nodes1 = {node for edge in weights1 for node in edge}
    nodes2 = {node for edge in weights2 for node in edge}
    total_node_weight = len(nodes1) + len(nodes2)
    total_weight = total_edge_weight + total_node_weight

    if total_weight == 0:
        # Both graphs are empty, consider them identical
        return 0.0

    return edit_distance / total_weight




class CrossExampleVisualizer:
    """Cross-example comparison visualizer."""

    def __init__(self, collector: MultiExampleCollector):
        self.collector = collector

    def _paper_rc_params(self) -> Dict[str, object]:
        return {
            'font.family': 'serif',
            'font.serif': ['Times New Roman', 'Times', 'Nimbus Roman', 'DejaVu Serif'],
            'font.size': 10,
            'axes.titlesize': 11,
            'axes.labelsize': 10,
            'xtick.labelsize': 9,
            'ytick.labelsize': 9,
            'legend.fontsize': 9,
            'axes.linewidth': 0.8,
            'grid.linewidth': 0.6,
            'lines.linewidth': 1.2,
            'axes.spines.top': True,
            'axes.spines.right': True,
            'axes.spines.left': True,
            'axes.spines.bottom': True,
            'axes.facecolor': 'white',
            'figure.facecolor': 'white',
            'savefig.facecolor': 'white',
            'pdf.fonttype': 42,
            'ps.fonttype': 42,
        }

    def _similarity_plot_style(self) -> Dict[str, object]:
        return {
            'label_size': 10,
            'title_size': 11,
            'tick_size': 9,
            'annotation_size': 8,
            'table_fontsize': 8,
            'label_weight': 'normal',
            'title_weight': 'semibold',
            'grid_alpha': 0.25,
            'violin_alpha': 0.35,
            'violin_width': 0.7,
            'violin_linewidths': {
                'cmeans': 1.1,
                'cmedians': 1.1,
                'cmins': 0.9,
                'cmaxes': 0.9,
                'cbars': 0.9,
            },
            'x_tick_rotation': 35,
            'x_tick_ha': 'right',
            'single_figsize': (6.4, 4.4),
            'duo_figsize': (12.0, 4.4),
            'triplet_figsize': (12.8, 4.4),
            'combined_figsize': (18, 12.5),
        }

    def _similarity_panel_specs(self) -> List[Dict[str, str]]:
        return [
            {
                'data_key': 'pairwise_jaccard_values',
                'color': '#27ae60',
                'ylabel': 'Pairwise Jaccard',
                'title_template': 'Pairwise Jaccard Similarity\n(Edge Sets, Unweighted)',
                'file_suffix': 'avg_pairwise_jaccard',
            },
            {
                'data_key': 'pairwise_lcs_values',
                'color': '#8e44ad',
                'ylabel': 'Pairwise LCS',
                'title_template': 'Pairwise LCS Similarity\n(Within Each {scope_label} Across Runs)',
                'file_suffix': 'avg_pairwise_lcs',
            },
            {
                'data_key': 'pairwise_ged_values',
                'color': '#2e86c1',
                'ylabel': 'Pairwise nGED',
                'title_template': 'Pairwise Normalized GED\n(Normalized by max sequence length)',
                'file_suffix': 'avg_pairwise_norm_ged',
            },
        ]

    def _plot_similarity_violin_panel(
        self,
        ax,
        plot_data: List[List[float]],
        example_names: List[str],
        color: str,
        scope_label: str,
        ylabel: str,
        title: str,
        style: Dict[str, object],
    ) -> None:
        positions = np.arange(len(example_names)) + 1
        parts = ax.violinplot(
            plot_data,
            positions=positions,
            points=100,
            widths=style['violin_width'],
            bw_method='silverman',
        )
        for body in parts['bodies']:
            body.set_facecolor(color)
            body.set_alpha(style['violin_alpha'])
        for key, width in style['violin_linewidths'].items():
            if key in parts:
                parts[key].set_color('black')
                parts[key].set_linewidth(width)
        ax.set_xticks(positions)
        ax.set_xticklabels(
            example_names,
            rotation=style['x_tick_rotation'],
            ha=style['x_tick_ha'],
        )
        ax.set_xlabel(scope_label, fontsize=style['label_size'], fontweight=style['label_weight'])
        ax.set_ylabel(ylabel, fontsize=style['label_size'], fontweight=style['label_weight'])
        if title:
            ax.set_title(title, fontsize=style['title_size'], fontweight=style['title_weight'])
        ax.tick_params(axis='both', labelsize=style['tick_size'])
        ax.set_frame_on(True)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color('black')
            spine.set_linewidth(style['violin_linewidths'].get('cbars', 0.9))
        ax.grid(
            axis='y',
            alpha=style['grid_alpha'],
            color='#c0c0c0',
            linestyle='--',
            linewidth=0.6,
        )

    def _collect_call_graph_similarity_data(
        self,
        examples: Optional[List[str]] = None
    ) -> Optional[Dict[str, object]]:
        if examples is None:
            examples = list(self.collector.examples.keys())

        if len(examples) < 1:
            print("Warning: Need at least 1 example for comparison")
            return None

        # Collect average pairwise normalized graph edit distance for each example
        example_avg_ged = {}
        example_avg_lcs = {}
        example_avg_jaccard = {}
        example_ged_std = {}
        example_lcs_std = {}
        example_jaccard_std = {}
        example_pairwise_ged = {}
        example_pairwise_lcs = {}
        example_pairwise_jaccard = {}
        example_num_runs = {}
        example_graph_sizes = {}
        example_graph_details = {}

        for name in examples:
            example = self.collector.examples[name]

            # Get average pairwise normalized graph edit distance
            if example.average_pairwise_normalized_ged is not None:
                example_avg_ged[name] = example.average_pairwise_normalized_ged
                example_avg_lcs[name] = example.average_pairwise_lcs if example.average_pairwise_lcs is not None else 0.0
                example_avg_jaccard[name] = example.average_pairwise_jaccard if example.average_pairwise_jaccard is not None else 0.0
                example_ged_std[name] = example.average_pairwise_ged_std if example.average_pairwise_ged_std is not None else 0.0
                example_lcs_std[name] = example.average_pairwise_lcs_std if example.average_pairwise_lcs_std is not None else 0.0
                example_jaccard_std[name] = example.average_pairwise_jaccard_std if example.average_pairwise_jaccard_std is not None else 0.0
                example_pairwise_ged[name] = example.pairwise_normalized_ged if example.pairwise_normalized_ged else [0.0]
                example_pairwise_lcs[name] = example.pairwise_lcs if example.pairwise_lcs else [0.0]
                example_pairwise_jaccard[name] = example.pairwise_jaccard if example.pairwise_jaccard else [0.0]
                example_num_runs[name] = len(example.call_graphs_per_run)

                # Compute average graph size across all runs
                if example.call_graphs_per_run:
                    # Collect detailed information (node and edge counts)
                    all_nodes = set()
                    all_edges = set()
                    node_counts = []
                    edge_counts = []
                    for graph in example.call_graphs_per_run:
                        nodes_in_graph = set()
                        for source, target in graph:
                            nodes_in_graph.add(source)
                            nodes_in_graph.add(target)
                            all_nodes.add(source)
                            all_nodes.add(target)
                            all_edges.add((source, target))
                        node_counts.append(len(nodes_in_graph))
                        edge_counts.append(len(graph))
                    example_graph_details[name] = {
                        'avg_nodes': np.mean(node_counts) if node_counts else 0,
                        'avg_edges': np.mean(edge_counts) if edge_counts else 0,
                        'unique_nodes': len(all_nodes),
                        'unique_edges': len(all_edges)
                    }
                    example_graph_sizes[name] = (
                        example_graph_details[name]['avg_nodes'] +
                        example_graph_details[name]['avg_edges']
                    )
                else:
                    example_graph_sizes[name] = 0
                    example_graph_details[name] = {'avg_nodes': 0, 'avg_edges': 0, 'unique_nodes': 0, 'unique_edges': 0}
            else:
                print(f"Warning: No call graph similarity data found for example '{name}' (may need per_run mode)")

        if not example_avg_ged:
            print("Warning: No examples with call graph similarity data found")
            return None

        example_names = list(example_avg_ged.keys())
        return {
            'example_names': example_names,
            'avg_ged_values': [example_avg_ged[name] for name in example_names],
            'avg_lcs_values': [example_avg_lcs[name] for name in example_names],
            'avg_jaccard_values': [example_avg_jaccard[name] for name in example_names],
            'ged_std_values': [example_ged_std[name] for name in example_names],
            'lcs_std_values': [example_lcs_std[name] for name in example_names],
            'jaccard_std_values': [example_jaccard_std[name] for name in example_names],
            'pairwise_ged_values': [example_pairwise_ged[name] for name in example_names],
            'pairwise_lcs_values': [example_pairwise_lcs[name] for name in example_names],
            'pairwise_jaccard_values': [example_pairwise_jaccard[name] for name in example_names],
            'num_runs': [example_num_runs[name] for name in example_names],
            'graph_sizes': [example_graph_sizes[name] for name in example_names],
            'avg_nodes': [example_graph_details[name]['avg_nodes'] for name in example_names],
            'avg_edges': [example_graph_details[name]['avg_edges'] for name in example_names],
            'example_graph_details': example_graph_details,
        }

    def plot_call_graph_similarity_violins(
        self,
        output_file: str,
        examples: Optional[List[str]] = None,
        x_label: str = 'Model'
    ):
        """Plot call graph similarity violins only."""
        data = self._collect_call_graph_similarity_data(examples)
        if data is None:
            return

        example_names = data['example_names']
        pairwise_ged_values = data['pairwise_ged_values']
        pairwise_lcs_values = data['pairwise_lcs_values']
        pairwise_jaccard_values = data['pairwise_jaccard_values']
        scope_label = x_label if x_label else 'Example'

        style = self._similarity_plot_style()
        panel_specs = self._similarity_panel_specs()
        plot_data_map = {
            'pairwise_ged_values': pairwise_ged_values,
            'pairwise_lcs_values': pairwise_lcs_values,
            'pairwise_jaccard_values': pairwise_jaccard_values,
        }

        with plt.rc_context(self._paper_rc_params()):
            fig, axes = plt.subplots(1, 3, figsize=style['triplet_figsize'])
            for ax, spec in zip(axes, panel_specs):
                title = spec['title_template'].format(scope_label=scope_label)
                self._plot_similarity_violin_panel(
                    ax,
                    plot_data_map[spec['data_key']],
                    example_names,
                    spec['color'],
                    scope_label,
                    spec['ylabel'],
                    title,
                    style,
                )
            fig.tight_layout()
            fig.savefig(output_file, dpi=300, bbox_inches='tight')
            plt.close(fig)
        print(f"Call graph similarity violin plot saved to: {output_file}")

    def plot_call_graph_similarity_pairwise_panels(
        self,
        output_dir: str,
        examples: Optional[List[str]] = None,
        x_label: str = 'Example',
        filename_prefix: str = 'call_graph_similarity',
        include_ged: bool = False,
    ) -> None:
        """Plot average pairwise similarity violins (Jaccard+LCS combined, GED separate)."""
        data = self._collect_call_graph_similarity_data(examples)
        if data is None:
            return

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        example_names = data['example_names']
        scope_label = x_label if x_label else 'Example'
        panel_specs = self._similarity_panel_specs()
        style = self._similarity_plot_style()

        spec_map = {spec['file_suffix']: spec for spec in panel_specs}
        jaccard_spec = spec_map['avg_pairwise_jaccard']
        lcs_spec = spec_map['avg_pairwise_lcs']
        ged_spec = spec_map['avg_pairwise_norm_ged']

        with plt.rc_context(self._paper_rc_params()):
            for spec in (jaccard_spec, lcs_spec):
                fig, ax = plt.subplots(1, 1, figsize=style['single_figsize'])
                self._plot_similarity_violin_panel(
                    ax,
                    data[spec['data_key']],
                    example_names,
                    spec['color'],
                    scope_label,
                    spec['ylabel'],
                    "",
                    style,
                )
                fig.tight_layout()
                output_file = output_path / f"{filename_prefix}_{spec['file_suffix']}.pdf"
                fig.savefig(output_file, dpi=300, bbox_inches='tight')
                plt.close(fig)
                print(f"Call graph similarity panel saved to: {output_file}")

            if include_ged:
                fig, ax = plt.subplots(1, 1, figsize=style['single_figsize'])
                self._plot_similarity_violin_panel(
                    ax,
                    data[ged_spec['data_key']],
                    example_names,
                    ged_spec['color'],
                    scope_label,
                    ged_spec['ylabel'],
                    "",
                    style,
                )
                fig.tight_layout()
                output_file = output_path / f"{filename_prefix}_{ged_spec['file_suffix']}.pdf"
                fig.savefig(output_file, dpi=300, bbox_inches='tight')
                plt.close(fig)
                print(f"Call graph similarity panel saved to: {output_file}")

        avg_jaccard_values = data.get('avg_jaccard_values', [])
        avg_lcs_values = data.get('avg_lcs_values', [])
        if avg_jaccard_values and avg_lcs_values:
            mean_jaccard = float(np.mean(avg_jaccard_values))
            mean_lcs = float(np.mean(avg_lcs_values))
            print(
                "Overall mean across examples: "
                f"Jaccard={mean_jaccard:.4f}, LCS={mean_lcs:.4f}"
            )

    def plot_call_graph_similarity(
        self,
        output_file: str,
        examples: Optional[List[str]] = None
    ):
        """Plot call graph similarity comparison.

        Computes call graph similarity for each example (average pairwise normalized graph edit distance
        across all runs within each example) and displays results in a single figure.

        Args:
            output_file: Output file path
            examples: List of examples to compare (None means all examples)
        """
        data = self._collect_call_graph_similarity_data(examples)
        if data is None:
            return

        # Prepare data for visualization
        example_names = data['example_names']
        avg_ged_values = data['avg_ged_values']
        avg_lcs_values = data['avg_lcs_values']
        avg_jaccard_values = data['avg_jaccard_values']
        ged_std_values = data['ged_std_values']
        lcs_std_values = data['lcs_std_values']
        jaccard_std_values = data['jaccard_std_values']
        pairwise_ged_values = data['pairwise_ged_values']
        pairwise_lcs_values = data['pairwise_lcs_values']
        pairwise_jaccard_values = data['pairwise_jaccard_values']
        num_runs = data['num_runs']
        graph_sizes = data['graph_sizes']
        avg_nodes = data['avg_nodes']
        avg_edges = data['avg_edges']
        example_graph_details = data['example_graph_details']

        style = self._similarity_plot_style()
        panel_specs = self._similarity_panel_specs()
        scope_label = 'Example'

        with plt.rc_context(self._paper_rc_params()):
            # Create figure
            fig = plt.figure(figsize=style['combined_figsize'])
            from matplotlib.gridspec import GridSpec
            gs = GridSpec(3, 3, figure=fig, wspace=0.3, hspace=0.35)

            # Chart 1-3: Average pairwise similarity violins
            axes_top = [fig.add_subplot(gs[0, idx]) for idx in range(3)]
            for ax, spec in zip(axes_top, panel_specs):
                title = spec['title_template'].format(scope_label=scope_label)
                self._plot_similarity_violin_panel(
                    ax,
                    data[spec['data_key']],
                    example_names,
                    spec['color'],
                    scope_label,
                    spec['ylabel'],
                    title,
                    style,
                )

            # Chart 4: Number of runs comparison
            ax4 = fig.add_subplot(gs[1, 0])
            bars_runs = ax4.bar(
                example_names,
                num_runs,
                color='#e74c3c',
                alpha=0.8,
                edgecolor='black',
                linewidth=1.2,
            )
            ax4.set_xlabel(scope_label, fontsize=style['label_size'], fontweight=style['label_weight'])
            ax4.set_ylabel('Number of Runs', fontsize=style['label_size'], fontweight=style['label_weight'])
            ax4.set_title('Number of Runs per Example', fontsize=style['title_size'], fontweight=style['title_weight'])
            ax4.set_xticks(np.arange(len(example_names)))
            ax4.set_xticklabels(example_names, rotation=style['x_tick_rotation'], ha=style['x_tick_ha'])
            ax4.tick_params(axis='both', labelsize=style['tick_size'])
            ax4.grid(axis='y', alpha=style['grid_alpha'])

            # Add value labels
            for bar, num in zip(bars_runs, num_runs):
                height = bar.get_height()
                ax4.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    height,
                    f'{int(num)}',
                    ha='center',
                    va='bottom',
                    fontsize=style['annotation_size'],
                )

            # Chart 5: Call graph size comparison (nodes vs edges)
            ax5 = fig.add_subplot(gs[1, 1])
            graph_info = []
            for name in example_names:
                details = example_graph_details[name]
                graph_info.append(f'{details["avg_nodes"]:.1f} nodes\n{details["avg_edges"]:.1f} edges')

            bars3_nodes = ax5.bar(
                example_names,
                avg_nodes,
                color='orange',
                alpha=0.85,
                edgecolor='black',
                linewidth=1.2,
                label='Avg Nodes',
            )
            bars3_edges = ax5.bar(
                example_names,
                avg_edges,
                bottom=avg_nodes,
                color='#27ae60',
                alpha=0.85,
                edgecolor='black',
                linewidth=1.2,
                label='Avg Edges',
            )
            ax5.set_xlabel(scope_label, fontsize=style['label_size'], fontweight=style['label_weight'])
            ax5.set_ylabel('Average Count', fontsize=style['label_size'], fontweight=style['label_weight'])
            ax5.set_title('Average Call Graph Size per Run', fontsize=style['title_size'], fontweight=style['title_weight'])
            ax5.set_xticks(np.arange(len(example_names)))
            ax5.set_xticklabels(example_names, rotation=style['x_tick_rotation'], ha=style['x_tick_ha'])
            ax5.tick_params(axis='both', labelsize=style['tick_size'])
            ax5.grid(axis='y', alpha=style['grid_alpha'])
            ax5.legend(loc='upper right', fontsize=style['tick_size'])

            # Add detailed information labels
            for total, info, bar in zip(graph_sizes, graph_info, bars3_nodes):
                height = total
                ax5.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    height,
                    info,
                    ha='center',
                    va='bottom',
                    fontsize=style['annotation_size'],
                )

            # Chart 6: Statistics table
            ax6 = fig.add_subplot(gs[2, :])
            ax6.axis('off')

            # Prepare table data
            stats_data = [['Example', 'Avg Norm GED (calls)', 'Avg Pair LCS', 'Avg Pair Jaccard', '# Runs', 'Avg Size (N+E)', 'Avg Nodes', 'Avg Edges']]
            for name in example_names:
                details = example_graph_details[name]
                stats_data.append([
                    name,
                    f'{avg_ged_values[example_names.index(name)]:.4f}',
                    f'{avg_lcs_values[example_names.index(name)]:.4f}',
                    f'{avg_jaccard_values[example_names.index(name)]:.4f}',
                    str(int(num_runs[example_names.index(name)])),
                    f'{graph_sizes[example_names.index(name)]:.1f}',
                    f'{details["avg_nodes"]:.1f}',
                    f'{details["avg_edges"]:.1f}'
                ])

            table = ax6.table(cellText=stats_data[1:], colLabels=stats_data[0],
                             cellLoc='center', loc='center')
            table.auto_set_font_size(False)
            table.set_fontsize(style['table_fontsize'])
            table.scale(1, 1.8)
            ax6.set_title(
                'Call Graph Similarity Statistics',
                fontsize=style['title_size'],
                fontweight=style['title_weight'],
                pad=16,
            )

            fig.tight_layout()
            fig.savefig(output_file, dpi=300, bbox_inches='tight')
            plt.close(fig)
            print(f"Call graph similarity comparison plot saved to: {output_file}")

    def plot_token_comparison(self, output_file: str, examples: Optional[List[str]] = None):
        """Compare token consumption across multiple examples.

        Args:
            output_file: Output file path
            examples: List of examples to compare (None means all examples)
        """
        if examples is None:
            examples = list(self.collector.examples.keys())

        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        # Collect data
        example_names = []
        total_tokens = []
        prompt_tokens = []
        completion_tokens = []

        for name in examples:
            tokens = self.collector.get_metric(name, 'tokens')
            if tokens:
                example_names.append(name)
                total_tokens.append(tokens['total']['total'])
                prompt_tokens.append(tokens['total']['prompt'])
                completion_tokens.append(tokens['total']['completion'])

        if not example_names:
            print("Warning: No token data found for comparison")
            return

        # Chart 1: Total token comparison (stacked bar chart)
        ax1 = axes[0, 0]
        x = np.arange(len(example_names))
        width = 0.6
        ax1.bar(x, prompt_tokens, width, label='Prompt Tokens', color='#3498db', alpha=0.8)
        ax1.bar(x, completion_tokens, width, bottom=prompt_tokens,
                label='Completion Tokens', color='#e74c3c', alpha=0.8)
        ax1.set_xlabel('Example', fontsize=12, fontweight='bold')
        ax1.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax1.set_title('Token Consumption Comparison Across Examples', fontsize=14, fontweight='bold')
        ax1.set_xticks(x)
        ax1.set_xticklabels(example_names, rotation=45, ha='right')
        ax1.legend()
        ax1.grid(axis='y', alpha=0.3)

        # Chart 2: Total token comparison (side-by-side bar chart)
        ax2 = axes[0, 1]
        width = 0.35
        ax2.bar(x - width/2, prompt_tokens, width, label='Prompt Tokens', color='#3498db', alpha=0.8)
        ax2.bar(x + width/2, completion_tokens, width, label='Completion Tokens', color='#e74c3c', alpha=0.8)
        ax2.set_xlabel('Example', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax2.set_title('Token Consumption Comparison (Side-by-Side)', fontsize=14, fontweight='bold')
        ax2.set_xticks(x)
        ax2.set_xticklabels(example_names, rotation=45, ha='right')
        ax2.legend()
        ax2.grid(axis='y', alpha=0.3)

        # Chart 3 removed (per-agent token comparison was too crowded)
        # Chart 3 (now statistics table)
        ax3 = axes[1, 0]
        ax3.axis('off')
        stats_data = [['Example', 'Total Tokens', 'Prompt', 'Completion']]
        for name in examples:
            tokens = self.collector.get_metric(name, 'tokens')
            if tokens:
                stats_data.append([
                    name,
                    f"{tokens['total']['total']:,}",
                    f"{tokens['total']['prompt']:,}",
                    f"{tokens['total']['completion']:,}"
                ])
        table = ax3.table(cellText=stats_data[1:], colLabels=stats_data[0],
                         cellLoc='center', loc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1, 2)
        ax3.set_title('Token Statistics Summary', fontsize=14, fontweight='bold', pad=20)

        # Chart 4 left blank to avoid overcrowding
        ax4 = axes[1, 1]
        ax4.axis('off')

        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"Token comparison plot saved to: {output_file}")

    def plot_custom_comparison(
        self,
        metric_extractor: callable,
        plot_function: callable,
        output_file: str,
        examples: Optional[List[str]] = None
    ):
        """Custom comparison visualization.

        Args:
            metric_extractor: Function that takes ExampleMetrics object and returns metric value to compare
            plot_function: Function that takes (metric_values_dict, output_path) and performs plotting
            output_file: Output file path
            examples: List of examples to compare
        """
        if examples is None:
            examples = list(self.collector.examples.keys())

        # Extract metrics for each example
        metric_values = {}
        for name in examples:
            example = self.collector.examples[name]
            metric_values[name] = metric_extractor(example)

        # Call custom plotting function
        plot_function(metric_values, output_file)
