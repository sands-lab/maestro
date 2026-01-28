"""Per-run analysis utilities for academic research."""

import numpy as np
import re
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from pathlib import Path

from .extractors import (
    extract_token_consumption,
    extract_delay_breakdown,
    extract_message_sizes,
    extract_cpu_memory_usage,
    extract_call_graph,
    extract_call_sequence,
)
from .data_loaders import load_traces, load_metrics


def analyze_per_run(trace_files: List[str], metrics_files: Optional[List[str]] = None) -> Dict:
    """Analyze each run separately and compute statistics across runs.

    Args:
        trace_files: List of trace file paths
        metrics_files: Optional list of metrics file paths

    Returns:
        Dictionary containing per-run data and cross-run statistics
    """
    results = {
        'runs': [],
        'statistics': {}
    }

    # Analyze each run
    for i, trace_file in enumerate(trace_files):
        traces = load_traces(trace_file)
        metrics = load_metrics(metrics_files[i]) if metrics_files and i < len(metrics_files) else []

        # Extract metrics for this run
        run_data = {
            'run_id': i + 1,
            'trace_file': Path(trace_file).name,
            'traces': traces,
            'metrics': metrics,
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
        if metrics:
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


def compute_cross_run_statistics(runs: List[Dict]) -> Dict:
    """Compute statistics across multiple runs.

    Returns:
        Dictionary with statistics for each metric type
    """
    stats = {}

    # E2E Delay statistics
    e2e_delays = []
    for run in runs:
        e2e_delay_list = run['delays']['component'].get('e2e_delay', [])
        if e2e_delay_list:
            e2e_delays.append(e2e_delay_list[0])  # Single value per run

    if e2e_delays:
        stats['e2e_delay'] = {
            'values': e2e_delays,
            'mean': np.mean(e2e_delays),
            'std': np.std(e2e_delays),
            'min': np.min(e2e_delays),
            'max': np.max(e2e_delays),
            'p50': np.percentile(e2e_delays, 50),
            'p95': np.percentile(e2e_delays, 95),
            'p99': np.percentile(e2e_delays, 99),
            'cv': np.std(e2e_delays) / np.mean(e2e_delays) if np.mean(e2e_delays) > 0 else 0,
            'n': len(e2e_delays)
        }

    # Token consumption statistics (total across all runs)
    total_prompt = sum(run['tokens']['total']['prompt'] for run in runs)
    total_completion = sum(run['tokens']['total']['completion'] for run in runs)
    total_tokens = sum(run['tokens']['total']['total'] for run in runs)

    # Per-run token totals
    run_token_totals = [run['tokens']['total']['total'] for run in runs]
    run_token_prompt = [run['tokens']['total']['prompt'] for run in runs]
    run_token_completion = [run['tokens']['total']['completion'] for run in runs]

    stats['tokens'] = {
        'total_prompt': total_prompt,
        'total_completion': total_completion,
        'total_tokens': total_tokens,
        'per_run_totals': run_token_totals,
        'per_run_prompt': run_token_prompt,
        'per_run_completion': run_token_completion,
        'mean_per_run': np.mean(run_token_totals) if run_token_totals else 0,
        'std_per_run': np.std(run_token_totals) if run_token_totals else 0,
        'mean_prompt': np.mean(run_token_prompt) if run_token_prompt else 0,
        'std_prompt': np.std(run_token_prompt) if run_token_prompt else 0,
        'mean_completion': np.mean(run_token_completion) if run_token_completion else 0,
        'std_completion': np.std(run_token_completion) if run_token_completion else 0,
        'n': len(runs)
    }

    # Agent-LLM delay statistics (collect all delays across all runs)
    all_llm_delays = []
    for run in runs:
        all_llm_delays.extend(run['delays']['component'].get('agent_llm_delay', []))

    if all_llm_delays:
        stats['agent_llm_delay'] = {
            'values': all_llm_delays,
            'mean': np.mean(all_llm_delays),
            'std': np.std(all_llm_delays),
            'min': np.min(all_llm_delays),
            'max': np.max(all_llm_delays),
            'p50': np.percentile(all_llm_delays, 50),
            'p95': np.percentile(all_llm_delays, 95),
            'p99': np.percentile(all_llm_delays, 99),
            'n': len(all_llm_delays)
        }

    # Inter-agent delay statistics
    all_inter_delays = []
    for run in runs:
        all_inter_delays.extend(run['delays']['component'].get('inter_agent_delay', []))

    if all_inter_delays:
        stats['inter_agent_delay'] = {
            'values': all_inter_delays,
            'mean': np.mean(all_inter_delays),
            'std': np.std(all_inter_delays),
            'min': np.min(all_inter_delays),
            'max': np.max(all_inter_delays),
            'p50': np.percentile(all_inter_delays, 50),
            'p95': np.percentile(all_inter_delays, 95),
            'p99': np.percentile(all_inter_delays, 99),
            'n': len(all_inter_delays)
        }

    # Processing delay statistics
    all_proc_delays = []
    for run in runs:
        all_proc_delays.extend(run['delays']['component'].get('agent_processing_delay', []))

    if all_proc_delays:
        stats['agent_processing_delay'] = {
            'values': all_proc_delays,
            'mean': np.mean(all_proc_delays),
            'std': np.std(all_proc_delays),
            'min': np.min(all_proc_delays),
            'max': np.max(all_proc_delays),
            'p50': np.percentile(all_proc_delays, 50),
            'p95': np.percentile(all_proc_delays, 95),
            'p99': np.percentile(all_proc_delays, 99),
            'n': len(all_proc_delays)
        }

    # CPU and Memory usage statistics
    # For time-series data, we compute per-run statistics (mean, peak, min) first,
    # then analyze the distribution of these statistics across runs
    cpu_per_run_mean = []
    cpu_per_run_peak = []
    cpu_per_run_min = []
    memory_per_run_mean = []
    memory_per_run_peak = []
    memory_per_run_min = []

    for run in runs:
        if run.get('resource_usage'):
            usage = run['resource_usage']

            # CPU statistics per run
            cpu_data = usage.get('cpu', {}).get('process', [])
            if cpu_data:
                cpu_values = [v for _, v in cpu_data]
                if cpu_values:
                    cpu_per_run_mean.append(np.mean(cpu_values))
                    cpu_per_run_peak.append(np.max(cpu_values))
                    cpu_per_run_min.append(np.min(cpu_values))

            # Memory statistics per run
            memory_data = usage.get('memory', {}).get('process', [])
            if memory_data:
                memory_values = [v for _, v in memory_data]
                if memory_values:
                    memory_per_run_mean.append(np.mean(memory_values))
                    memory_per_run_peak.append(np.max(memory_values))
                    memory_per_run_min.append(np.min(memory_values))

    if cpu_per_run_mean:
        stats['cpu_usage'] = {
            'mean': {
                'values': cpu_per_run_mean,
                'mean': np.mean(cpu_per_run_mean),
                'std': np.std(cpu_per_run_mean),
                'min': np.min(cpu_per_run_mean),
                'max': np.max(cpu_per_run_mean),
                'p50': np.percentile(cpu_per_run_mean, 50),
                'p95': np.percentile(cpu_per_run_mean, 95),
                'p99': np.percentile(cpu_per_run_mean, 99),
                'n': len(cpu_per_run_mean)
            },
            'peak': {
                'values': cpu_per_run_peak,
                'mean': np.mean(cpu_per_run_peak),
                'std': np.std(cpu_per_run_peak),
                'min': np.min(cpu_per_run_peak),
                'max': np.max(cpu_per_run_peak),
                'p50': np.percentile(cpu_per_run_peak, 50),
                'p95': np.percentile(cpu_per_run_peak, 95),
                'p99': np.percentile(cpu_per_run_peak, 99),
                'n': len(cpu_per_run_peak)
            },
            'min': {
                'values': cpu_per_run_min,
                'mean': np.mean(cpu_per_run_min),
                'std': np.std(cpu_per_run_min),
                'min': np.min(cpu_per_run_min),
                'max': np.max(cpu_per_run_min),
                'p50': np.percentile(cpu_per_run_min, 50),
                'p95': np.percentile(cpu_per_run_min, 95),
                'p99': np.percentile(cpu_per_run_min, 99),
                'n': len(cpu_per_run_min)
            }
        }

    if memory_per_run_mean:
        stats['memory_usage'] = {
            'mean': {
                'values': memory_per_run_mean,
                'mean': np.mean(memory_per_run_mean),
                'std': np.std(memory_per_run_mean),
                'min': np.min(memory_per_run_mean),
                'max': np.max(memory_per_run_mean),
                'p50': np.percentile(memory_per_run_mean, 50),
                'p95': np.percentile(memory_per_run_mean, 95),
                'p99': np.percentile(memory_per_run_mean, 99),
                'n': len(memory_per_run_mean)
            },
            'peak': {
                'values': memory_per_run_peak,
                'mean': np.mean(memory_per_run_peak),
                'std': np.std(memory_per_run_peak),
                'min': np.min(memory_per_run_peak),
                'max': np.max(memory_per_run_peak),
                'p50': np.percentile(memory_per_run_peak, 50),
                'p95': np.percentile(memory_per_run_peak, 95),
                'p99': np.percentile(memory_per_run_peak, 99),
                'n': len(memory_per_run_peak)
            },
            'min': {
                'values': memory_per_run_min,
                'mean': np.mean(memory_per_run_min),
                'std': np.std(memory_per_run_min),
                'min': np.min(memory_per_run_min),
                'max': np.max(memory_per_run_min),
                'p50': np.percentile(memory_per_run_min, 50),
                'p95': np.percentile(memory_per_run_min, 95),
                'p99': np.percentile(memory_per_run_min, 99),
                'n': len(memory_per_run_min)
            }
        }

    # Message size statistics
    # For each run, compute total message sizes, then analyze across runs
    inter_agent_input_totals = []
    inter_agent_output_totals = []
    agent_llm_input_totals = []
    agent_llm_output_totals = []

    # Per-agent message size totals per run
    agent_message_totals_per_run = defaultdict(list)  # {agent_name: [total_size_per_run]}

    # Per agent-pair message size totals per run
    pair_message_totals_per_run = defaultdict(list)  # {(src, tgt): [total_size_per_run]}

    # Per agent-LLM message size totals per run
    agent_llm_totals_per_run = defaultdict(list)  # {agent_name: [total_size_per_run]}

    for run in runs:
        msg_sizes = run['message_sizes']

        # Component totals
        comp = msg_sizes['component']
        inter_agent_input_totals.append(sum(comp.get('inter_agent_input', [])))
        inter_agent_output_totals.append(sum(comp.get('inter_agent_output', [])))
        agent_llm_input_totals.append(sum(comp.get('agent_llm_input', [])))
        agent_llm_output_totals.append(sum(comp.get('agent_llm_output', [])))

        # Per-agent totals
        for agent_name, agent_data in msg_sizes['per_agent'].items():
            total = sum(agent_data.get('input', [])) + sum(agent_data.get('output', []))
            if total > 0:
                agent_message_totals_per_run[agent_name].append(total)

        # Per agent-pair totals
        for (src, tgt), pair_data in msg_sizes['inter_agent'].items():
            total = sum(pair_data.get('input', [])) + sum(pair_data.get('output', []))
            if total > 0:
                pair_message_totals_per_run[(src, tgt)].append(total)

        # Per agent-LLM totals
        for agent_name, llm_data in msg_sizes['agent_llm'].items():
            total = sum(llm_data.get('input', [])) + sum(llm_data.get('output', []))
            if total > 0:
                agent_llm_totals_per_run[agent_name].append(total)

    if inter_agent_input_totals or inter_agent_output_totals or agent_llm_input_totals or agent_llm_output_totals:
        stats['message_sizes'] = {
            'inter_agent_input': {
                'values': inter_agent_input_totals,
                'mean': np.mean(inter_agent_input_totals) if inter_agent_input_totals else 0,
                'std': np.std(inter_agent_input_totals) if inter_agent_input_totals else 0,
                'min': np.min(inter_agent_input_totals) if inter_agent_input_totals else 0,
                'max': np.max(inter_agent_input_totals) if inter_agent_input_totals else 0,
                'p50': np.percentile(inter_agent_input_totals, 50) if inter_agent_input_totals else 0,
                'p95': np.percentile(inter_agent_input_totals, 95) if inter_agent_input_totals else 0,
                'n': len(inter_agent_input_totals)
            },
            'inter_agent_output': {
                'values': inter_agent_output_totals,
                'mean': np.mean(inter_agent_output_totals) if inter_agent_output_totals else 0,
                'std': np.std(inter_agent_output_totals) if inter_agent_output_totals else 0,
                'min': np.min(inter_agent_output_totals) if inter_agent_output_totals else 0,
                'max': np.max(inter_agent_output_totals) if inter_agent_output_totals else 0,
                'p50': np.percentile(inter_agent_output_totals, 50) if inter_agent_output_totals else 0,
                'p95': np.percentile(inter_agent_output_totals, 95) if inter_agent_output_totals else 0,
                'n': len(inter_agent_output_totals)
            },
            'agent_llm_input': {
                'values': agent_llm_input_totals,
                'mean': np.mean(agent_llm_input_totals) if agent_llm_input_totals else 0,
                'std': np.std(agent_llm_input_totals) if agent_llm_input_totals else 0,
                'min': np.min(agent_llm_input_totals) if agent_llm_input_totals else 0,
                'max': np.max(agent_llm_input_totals) if agent_llm_input_totals else 0,
                'p50': np.percentile(agent_llm_input_totals, 50) if agent_llm_input_totals else 0,
                'p95': np.percentile(agent_llm_input_totals, 95) if agent_llm_input_totals else 0,
                'n': len(agent_llm_input_totals)
            },
            'agent_llm_output': {
                'values': agent_llm_output_totals,
                'mean': np.mean(agent_llm_output_totals) if agent_llm_output_totals else 0,
                'std': np.std(agent_llm_output_totals) if agent_llm_output_totals else 0,
                'min': np.min(agent_llm_output_totals) if agent_llm_output_totals else 0,
                'max': np.max(agent_llm_output_totals) if agent_llm_output_totals else 0,
                'p50': np.percentile(agent_llm_output_totals, 50) if agent_llm_output_totals else 0,
                'p95': np.percentile(agent_llm_output_totals, 95) if agent_llm_output_totals else 0,
                'n': len(agent_llm_output_totals)
            },
            'per_agent_totals': dict(agent_message_totals_per_run),
            'per_pair_totals': dict(pair_message_totals_per_run),
            'per_agent_llm_totals': dict(agent_llm_totals_per_run)
        }

    # Call Graph Similarity Statistics (Multi-Graph Metrics)
    if len(runs) > 1:
        call_graphs = [run['call_graph'] for run in runs]

        # 1. Consecutive pairwise similarities (for backward compatibility)
        consecutive_similarities = []
        for i in range(len(call_graphs) - 1):
            edges1 = call_graphs[i]
            edges2 = call_graphs[i + 1]
            if edges1 or edges2:
                intersection = len(edges1 & edges2)
                union = len(edges1 | edges2)
                similarity = intersection / union if union > 0 else 0.0
                consecutive_similarities.append(similarity)
            else:
                consecutive_similarities.append(0.0)

        # 2. All pairwise similarities (for comprehensive analysis)
        all_pairwise_similarities = []
        similarity_matrix = []
        for i in range(len(call_graphs)):
            row = []
            for j in range(len(call_graphs)):
                if i == j:
                    similarity = 1.0  # Self-similarity is 1.0
                else:
                    edges1 = call_graphs[i]
                    edges2 = call_graphs[j]
                    if edges1 or edges2:
                        intersection = len(edges1 & edges2)
                        union = len(edges1 | edges2)
                        similarity = intersection / union if union > 0 else 0.0
                    else:
                        similarity = 0.0
                row.append(similarity)
                if i < j:  # Only store upper triangle to avoid duplicates
                    all_pairwise_similarities.append(similarity)
            similarity_matrix.append(row)

        # 3. Overall consistency: intersection of all graphs / union of all graphs
        all_edges_intersection = set.intersection(*call_graphs) if call_graphs else set()
        all_edges_union = set.union(*call_graphs) if call_graphs else set()
        overall_consistency = len(all_edges_intersection) / len(all_edges_union) if all_edges_union else 0.0

        # 4. Core edge ratio: edges that appear in all runs / total unique edges
        core_edge_ratio = len(all_edges_intersection) / len(all_edges_union) if all_edges_union else 0.0

        # 5. Stability index: standard deviation of pairwise similarities (lower = more stable)
        stability_index = np.std(all_pairwise_similarities) if all_pairwise_similarities else 0.0

        # 6. Mean pairwise similarity
        mean_pairwise_similarity = np.mean(all_pairwise_similarities) if all_pairwise_similarities else 0.0

        stats['call_graph_similarity'] = {
            # Consecutive pairwise (for backward compatibility)
            'jaccard_similarities': consecutive_similarities,
            'jaccard_mean': np.mean(consecutive_similarities) if consecutive_similarities else 0.0,
            'jaccard_min': np.min(consecutive_similarities) if consecutive_similarities else 0.0,
            'jaccard_max': np.max(consecutive_similarities) if consecutive_similarities else 0.0,
            # Multi-graph metrics
            'overall_consistency': overall_consistency,  # Intersection/Union of all graphs
            'core_edge_ratio': core_edge_ratio,  # Same as overall_consistency, but more descriptive name
            'core_edges': list(all_edges_intersection),  # Edges that appear in all runs
            'total_unique_edges': len(all_edges_union),  # Total unique edges across all runs
            'mean_pairwise_similarity': mean_pairwise_similarity,  # Mean of all pairwise similarities
            'stability_index': stability_index,  # Std dev of pairwise similarities (lower = more stable)
            'pairwise_similarities': all_pairwise_similarities,  # All pairwise similarities
            'similarity_matrix': similarity_matrix,  # Full similarity matrix
            'n_runs': len(call_graphs)
        }

    # Call Sequence Similarity Statistics (LCS - Sequence Order)
    if len(runs) > 1:
        call_sequences = [run.get('call_sequence', []) for run in runs]

        # LCS similarity between consecutive runs
        lcs_similarities = []
        for i in range(len(call_sequences) - 1):
            seq1 = call_sequences[i]
            seq2 = call_sequences[i + 1]

            if not seq1 and not seq2:
                lcs_similarities.append(1.0)  # Both empty, consider identical
            elif not seq1 or not seq2:
                lcs_similarities.append(0.0)  # One empty, one not
            else:
                # Calculate LCS length
                def lcs_length(s1, s2):
                    m, n = len(s1), len(s2)
                    dp = [[0] * (n + 1) for _ in range(m + 1)]
                    for i in range(1, m + 1):
                        for j in range(1, n + 1):
                            if s1[i-1] == s2[j-1]:
                                dp[i][j] = dp[i-1][j-1] + 1
                            else:
                                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
                    return dp[m][n]

                lcs_len = lcs_length(seq1, seq2)
                max_len = max(len(seq1), len(seq2))
                similarity = lcs_len / max_len if max_len > 0 else 0.0
                lcs_similarities.append(similarity)

        # All pairwise LCS similarities
        all_pairwise_lcs = []
        lcs_matrix = []
        for i in range(len(call_sequences)):
            row = []
            for j in range(len(call_sequences)):
                if i == j:
                    similarity = 1.0
                else:
                    seq1 = call_sequences[i]
                    seq2 = call_sequences[j]
                    if not seq1 and not seq2:
                        similarity = 1.0
                    elif not seq1 or not seq2:
                        similarity = 0.0
                    else:
                        def lcs_length(s1, s2):
                            m, n = len(s1), len(s2)
                            dp = [[0] * (n + 1) for _ in range(m + 1)]
                            for i in range(1, m + 1):
                                for j in range(1, n + 1):
                                    if s1[i-1] == s2[j-1]:
                                        dp[i][j] = dp[i-1][j-1] + 1
                                    else:
                                        dp[i][j] = max(dp[i-1][j], dp[i][j-1])
                            return dp[m][n]
                        lcs_len = lcs_length(seq1, seq2)
                        max_len = max(len(seq1), len(seq2))
                        similarity = lcs_len / max_len if max_len > 0 else 0.0
                row.append(similarity)
                if i < j:
                    all_pairwise_lcs.append(similarity)
            lcs_matrix.append(row)

        stats['call_sequence_similarity'] = {
            'lcs_similarities': lcs_similarities,
            'lcs_mean': np.mean(lcs_similarities) if lcs_similarities else 0.0,
            'lcs_min': np.min(lcs_similarities) if lcs_similarities else 0.0,
            'lcs_max': np.max(lcs_similarities) if lcs_similarities else 0.0,
            'mean_pairwise_lcs': np.mean(all_pairwise_lcs) if all_pairwise_lcs else 0.0,
            'lcs_stability_index': np.std(all_pairwise_lcs) if all_pairwise_lcs else 0.0,
            'pairwise_lcs_similarities': all_pairwise_lcs,
            'lcs_matrix': lcs_matrix,
            'n_runs': len(call_sequences)
        }

    return stats


def plot_per_run_statistics(results: Dict, output_dir: str):
    """Generate visualizations with per-run statistics and distributions."""
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt

    stats = results['statistics']
    runs = results['runs']
    n_runs = len(runs)

    # 1. E2E Delay Distribution (Box Plot)
    if 'e2e_delay' in stats:
        fig, axes = plt.subplots(2, 2, figsize=(16, 12))

        # Chart 1: E2E Delay Box Plot
        ax1 = axes[0, 0]
        e2e_data = stats['e2e_delay']
        bp = ax1.boxplot([e2e_data['values']], labels=['E2E Delay'], patch_artist=True,
                         widths=0.6, showmeans=True, meanline=True)
        bp['boxes'][0].set_facecolor('#3498db')
        bp['boxes'][0].set_alpha(0.7)
        ax1.set_ylabel('Delay (ms)', fontsize=12, fontweight='bold')
        ax1.set_title(f'E2E Delay Distribution\n(n={e2e_data["n"]} runs)', fontsize=14, fontweight='bold')
        ax1.grid(axis='y', alpha=0.3)

        # Add statistics text
        stats_text = f'Mean: {e2e_data["mean"]:.2f} ms\n'
        stats_text += f'Std: {e2e_data["std"]:.2f} ms\n'
        stats_text += f'Min: {e2e_data["min"]:.2f} ms\n'
        stats_text += f'Max: {e2e_data["max"]:.2f} ms\n'
        stats_text += f'p50: {e2e_data["p50"]:.2f} ms\n'
        stats_text += f'p95: {e2e_data["p95"]:.2f} ms\n'
        stats_text += f'CV: {e2e_data["cv"]:.3f}'
        ax1.text(0.02, 0.98, stats_text, transform=ax1.transAxes,
                verticalalignment='top', fontsize=10, family='monospace',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        # Chart 2: E2E Delay per Run
        ax2 = axes[0, 1]
        run_ids = list(range(1, n_runs + 1))
        e2e_values = e2e_data['values']
        ax2.plot(run_ids, e2e_values, 'o-', color='#3498db', linewidth=2, markersize=10,
                markerfacecolor='#3498db', markeredgecolor='white', markeredgewidth=1.5)
        ax2.set_xlabel('Run ID', fontsize=12, fontweight='bold')
        ax2.set_ylabel('E2E Delay (ms)', fontsize=12, fontweight='bold')
        ax2.set_title('E2E Delay per Run', fontsize=14, fontweight='bold')
        ax2.grid(alpha=0.3, linestyle='--')
        ax2.set_xticks(run_ids)

        # Chart 3: Component Delay Distributions
        ax3 = axes[1, 0]
        delay_data = []
        labels = []
        colors_list = []

        if 'agent_llm_delay' in stats:
            delay_data.append(stats['agent_llm_delay']['values'])
            labels.append('Agent-LLM')
            colors_list.append('#e74c3c')

        if 'inter_agent_delay' in stats:
            delay_data.append(stats['inter_agent_delay']['values'])
            labels.append('Inter-Agent')
            colors_list.append('#3498db')

        if 'agent_processing_delay' in stats:
            delay_data.append(stats['agent_processing_delay']['values'])
            labels.append('Processing')
            colors_list.append('#2ecc71')

        if delay_data:
            bp = ax3.boxplot(delay_data, labels=labels, patch_artist=True,
                            widths=0.6, showmeans=True, meanline=True)
            for patch, color in zip(bp['boxes'], colors_list):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            ax3.set_ylabel('Delay (ms)', fontsize=12, fontweight='bold')
            ax3.set_title('Component Delay Distributions\n(across all runs)', fontsize=14, fontweight='bold')
            ax3.grid(axis='y', alpha=0.3)

        # Chart 4: Statistics Table
        ax4 = axes[1, 1]
        ax4.axis('off')

        table_data = [['Metric', 'Mean ± Std', 'Min', 'Max', 'p95', 'CV']]

        if 'e2e_delay' in stats:
            e2e = stats['e2e_delay']
            table_data.append([
                'E2E Delay',
                f'{e2e["mean"]:.2f} ± {e2e["std"]:.2f}',
                f'{e2e["min"]:.2f}',
                f'{e2e["max"]:.2f}',
                f'{e2e["p95"]:.2f}',
                f'{e2e["cv"]:.3f}'
            ])

        if 'agent_llm_delay' in stats:
            llm = stats['agent_llm_delay']
            table_data.append([
                'Agent-LLM',
                f'{llm["mean"]:.2f} ± {llm["std"]:.2f}',
                f'{llm["min"]:.2f}',
                f'{llm["max"]:.2f}',
                f'{llm["p95"]:.2f}',
                '-'
            ])

        if 'inter_agent_delay' in stats:
            inter = stats['inter_agent_delay']
            table_data.append([
                'Inter-Agent',
                f'{inter["mean"]:.2f} ± {inter["std"]:.2f}',
                f'{inter["min"]:.2f}',
                f'{inter["max"]:.2f}',
                f'{inter["p95"]:.2f}',
                '-'
            ])

        if 'agent_processing_delay' in stats:
            proc = stats['agent_processing_delay']
            table_data.append([
                'Processing',
                f'{proc["mean"]:.2f} ± {proc["std"]:.2f}',
                f'{proc["min"]:.2f}',
                f'{proc["max"]:.2f}',
                f'{proc["p95"]:.2f}',
                '-'
            ])

        if len(table_data) > 1:
            table = ax4.table(cellText=table_data[1:], colLabels=table_data[0],
                            cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
            table.auto_set_font_size(False)
            table.set_fontsize(9)
            table.scale(1, 2)
            ax4.set_title('Delay Statistics Across Runs', fontsize=14, fontweight='bold', pad=20)

        plt.tight_layout()
        plt.savefig(str(Path(output_dir) / "2_delay_breakdown_per_run.pdf"), dpi=300, bbox_inches='tight')
        print(f"✓ Per-run delay breakdown chart saved to {Path(output_dir) / '2_delay_breakdown_per_run.pdf'}")
        plt.close()

    # 2. Token Consumption Statistics
    if 'tokens' in stats:
        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        token_stats = stats['tokens']

        # Chart 1: Total tokens across all runs
        ax1 = axes[0, 0]
        categories = ['Prompt', 'Completion', 'Total']
        values = [token_stats['total_prompt'], token_stats['total_completion'], token_stats['total_tokens']]
        colors = ['#3498db', '#e74c3c', '#2ecc71']
        ax1.bar(categories, values, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
        ax1.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax1.set_title(f'Total Token Consumption\n(across {token_stats["n"]} runs)', fontsize=14, fontweight='bold')
        ax1.grid(axis='y', alpha=0.3)
        for i, v in enumerate(values):
            if v > 0:
                ax1.text(i, v + max(values) * 0.01, f'{v:,}', ha='center', va='bottom', fontweight='bold')

        # Chart 2: Tokens per run (distribution) - Box plots for Prompt, Completion, and Total
        ax2 = axes[0, 1]
        box_data = []
        box_labels = []
        box_colors = []

        if token_stats['per_run_prompt']:
            box_data.append(token_stats['per_run_prompt'])
            box_labels.append('Prompt')
            box_colors.append('#3498db')

        if token_stats['per_run_completion']:
            box_data.append(token_stats['per_run_completion'])
            box_labels.append('Completion')
            box_colors.append('#e74c3c')

        if token_stats['per_run_totals']:
            box_data.append(token_stats['per_run_totals'])
            box_labels.append('Total')
            box_colors.append('#2ecc71')

        if box_data:
            bp = ax2.boxplot(box_data, labels=box_labels, patch_artist=True,
                           widths=0.6, showmeans=True, meanline=True)
            for patch, color in zip(bp['boxes'], box_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            ax2.set_ylabel('Token Count', fontsize=12, fontweight='bold')
            ax2.set_title('Token Consumption Distribution per Run', fontsize=14, fontweight='bold')
            ax2.grid(axis='y', alpha=0.3)

        # Chart 3: Tokens per run (line plot showing trend across runs)
        ax3 = axes[1, 0]
        run_ids = list(range(1, n_runs + 1))

        # Plot prompt, completion, and total tokens as separate lines
        if token_stats['per_run_prompt']:
            ax3.plot(run_ids, token_stats['per_run_prompt'], 'o-', color='#3498db',
                    linewidth=2, markersize=10, label='Prompt Tokens', markerfacecolor='#3498db',
                    markeredgecolor='white', markeredgewidth=1.5)
        if token_stats['per_run_completion']:
            ax3.plot(run_ids, token_stats['per_run_completion'], 's-', color='#e74c3c',
                    linewidth=2, markersize=10, label='Completion Tokens', markerfacecolor='#e74c3c',
                    markeredgecolor='white', markeredgewidth=1.5)
        if token_stats['per_run_totals']:
            ax3.plot(run_ids, token_stats['per_run_totals'], '^-', color='#2ecc71',
                    linewidth=2, markersize=10, label='Total Tokens', markerfacecolor='#2ecc71',
                    markeredgecolor='white', markeredgewidth=1.5)

        ax3.set_xlabel('Run ID', fontsize=12, fontweight='bold')
        ax3.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax3.set_title('Token Consumption Trend Across Runs', fontsize=14, fontweight='bold')
        ax3.legend(loc='best', fontsize=10)
        ax3.grid(alpha=0.3, linestyle='--')
        ax3.set_xticks(run_ids)

        # Chart 4: Statistics table
        ax4 = axes[1, 1]
        ax4.axis('off')
        table_data = [
            ['Metric', 'Value'],
            ['Total Prompt Tokens', f'{token_stats["total_prompt"]:,}'],
            ['Total Completion Tokens', f'{token_stats["total_completion"]:,}'],
            ['Total Tokens', f'{token_stats["total_tokens"]:,}'],
            ['Mean per Run', f'{token_stats["mean_per_run"]:.0f}'],
            ['Std per Run', f'{token_stats["std_per_run"]:.0f}'],
            ['Number of Runs', f'{token_stats["n"]}']
        ]
        table = ax4.table(cellText=table_data[1:], colLabels=table_data[0],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 2)
        ax4.set_title('Token Consumption Statistics', fontsize=14, fontweight='bold', pad=20)

        plt.tight_layout()
        plt.savefig(str(Path(output_dir) / "1_token_consumption_per_run.pdf"), dpi=300, bbox_inches='tight')
        print(f"✓ Per-run token consumption chart saved to {Path(output_dir) / '1_token_consumption_per_run.pdf'}")
        plt.close()

    # 3. CPU/Memory Usage Statistics
    if 'cpu_usage' in stats or 'memory_usage' in stats:
        fig, axes = plt.subplots(2, 2, figsize=(16, 10))

        # Chart 1: CPU Usage Distribution (Mean, Peak, Min)
        ax1 = axes[0, 0]
        if 'cpu_usage' in stats:
            cpu_stats = stats['cpu_usage']
            box_data = []
            box_labels = []
            box_colors = []

            if cpu_stats['mean']['values']:
                box_data.append(cpu_stats['mean']['values'])
                box_labels.append('Mean')
                box_colors.append('#3498db')
            if cpu_stats['peak']['values']:
                box_data.append(cpu_stats['peak']['values'])
                box_labels.append('Peak')
                box_colors.append('#e74c3c')
            if cpu_stats['min']['values']:
                box_data.append(cpu_stats['min']['values'])
                box_labels.append('Min')
                box_colors.append('#2ecc71')

            if box_data:
                bp = ax1.boxplot(box_data, labels=box_labels, patch_artist=True,
                               widths=0.6, showmeans=True, meanline=True)
                for patch, color in zip(bp['boxes'], box_colors):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.7)
                ax1.set_ylabel('CPU Usage (%)', fontsize=12, fontweight='bold')
                ax1.set_title('CPU Usage Distribution Across Runs', fontsize=14, fontweight='bold')
                ax1.grid(axis='y', alpha=0.3)

        # Chart 2: Memory Usage Distribution (Mean, Peak, Min)
        ax2 = axes[0, 1]
        if 'memory_usage' in stats:
            memory_stats = stats['memory_usage']
            box_data = []
            box_labels = []
            box_colors = []

            if memory_stats['mean']['values']:
                box_data.append(memory_stats['mean']['values'])
                box_labels.append('Mean')
                box_colors.append('#3498db')
            if memory_stats['peak']['values']:
                box_data.append(memory_stats['peak']['values'])
                box_labels.append('Peak')
                box_colors.append('#e74c3c')
            if memory_stats['min']['values']:
                box_data.append(memory_stats['min']['values'])
                box_labels.append('Min')
                box_colors.append('#2ecc71')

            if box_data:
                bp = ax2.boxplot(box_data, labels=box_labels, patch_artist=True,
                               widths=0.6, showmeans=True, meanline=True)
                for patch, color in zip(bp['boxes'], box_colors):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.7)
                ax2.set_ylabel('Memory Usage (MB)', fontsize=12, fontweight='bold')
                ax2.set_title('Memory Usage Distribution Across Runs', fontsize=14, fontweight='bold')
                ax2.grid(axis='y', alpha=0.3)

        # Chart 3: CPU Usage per Run (Mean values)
        ax3 = axes[1, 0]
        if 'cpu_usage' in stats and stats['cpu_usage']['mean']['values']:
            cpu_stats = stats['cpu_usage']
            run_ids = list(range(1, len(cpu_stats['mean']['values']) + 1))
            ax3.plot(run_ids, cpu_stats['mean']['values'], 'o-', color='#3498db',
                    linewidth=2, markersize=10, label='Mean CPU Usage',
                    markerfacecolor='#3498db', markeredgecolor='white', markeredgewidth=1.5)
            if cpu_stats['peak']['values']:
                ax3.plot(run_ids, cpu_stats['peak']['values'], 's-', color='#e74c3c',
                        linewidth=2, markersize=10, label='Peak CPU Usage',
                        markerfacecolor='#e74c3c', markeredgecolor='white', markeredgewidth=1.5)
            ax3.set_xlabel('Run ID', fontsize=12, fontweight='bold')
            ax3.set_ylabel('CPU Usage (%)', fontsize=12, fontweight='bold')
            ax3.set_title('CPU Usage Trend Across Runs', fontsize=14, fontweight='bold')
            ax3.legend(loc='best', fontsize=10)
            ax3.grid(alpha=0.3, linestyle='--')
            ax3.set_xticks(run_ids)

        # Chart 4: Memory Usage per Run (Mean values)
        ax4 = axes[1, 1]
        if 'memory_usage' in stats and stats['memory_usage']['mean']['values']:
            memory_stats = stats['memory_usage']
            run_ids = list(range(1, len(memory_stats['mean']['values']) + 1))
            ax4.plot(run_ids, memory_stats['mean']['values'], 'o-', color='#3498db',
                    linewidth=2, markersize=10, label='Mean Memory Usage',
                    markerfacecolor='#3498db', markeredgecolor='white', markeredgewidth=1.5)
            if memory_stats['peak']['values']:
                ax4.plot(run_ids, memory_stats['peak']['values'], 's-', color='#e74c3c',
                        linewidth=2, markersize=10, label='Peak Memory Usage',
                        markerfacecolor='#e74c3c', markeredgecolor='white', markeredgewidth=1.5)
            ax4.set_xlabel('Run ID', fontsize=12, fontweight='bold')
            ax4.set_ylabel('Memory Usage (MB)', fontsize=12, fontweight='bold')
            ax4.set_title('Memory Usage Trend Across Runs', fontsize=14, fontweight='bold')
            ax4.legend(loc='best', fontsize=10)
            ax4.grid(alpha=0.3, linestyle='--')
            ax4.set_xticks(run_ids)

        plt.tight_layout()
        plt.savefig(str(Path(output_dir) / "3_cpu_memory_usage_per_run.pdf"), dpi=300, bbox_inches='tight')
        print(f"✓ Per-run CPU/Memory usage chart saved to {Path(output_dir) / '3_cpu_memory_usage_per_run.pdf'}")
        plt.close()

    # 4. Message Size Statistics
    if 'message_sizes' in stats:
        msg_stats = stats['message_sizes']
        fig, axes = plt.subplots(2, 3, figsize=(20, 12))

        # Chart 1: Overall message size totals per run (box plots)
        ax1 = axes[0, 0]
        box_data = []
        box_labels = []
        box_colors = []

        if msg_stats.get('inter_agent_input', {}).get('values'):
            box_data.append(msg_stats['inter_agent_input']['values'])
            box_labels.append('Inter-Agent\nInput')
            box_colors.append('#3498db')
        if msg_stats.get('inter_agent_output', {}).get('values'):
            box_data.append(msg_stats['inter_agent_output']['values'])
            box_labels.append('Inter-Agent\nOutput')
            box_colors.append('#e74c3c')
        if msg_stats.get('agent_llm_input', {}).get('values'):
            box_data.append(msg_stats['agent_llm_input']['values'])
            box_labels.append('Agent-LLM\nInput')
            box_colors.append('#2ecc71')
        if msg_stats.get('agent_llm_output', {}).get('values'):
            box_data.append(msg_stats['agent_llm_output']['values'])
            box_labels.append('Agent-LLM\nOutput')
            box_colors.append('#f39c12')

        if box_data:
            bp = ax1.boxplot(box_data, labels=box_labels, patch_artist=True,
                           widths=0.6, showmeans=True, meanline=True)
            for patch, color in zip(bp['boxes'], box_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)
            ax1.set_ylabel('Total Message Size (KB)', fontsize=12, fontweight='bold')
            ax1.set_title('Message Size Distribution Across Runs', fontsize=14, fontweight='bold')
            ax1.grid(axis='y', alpha=0.3)

        # Chart 2: Message size totals per run (line plot)
        ax2 = axes[0, 1]
        run_ids = list(range(1, n_runs + 1))
        if msg_stats.get('inter_agent_input', {}).get('values'):
            ax2.plot(run_ids, msg_stats['inter_agent_input']['values'], 'o-', color='#3498db',
                    linewidth=2, markersize=10, label='Inter-Agent Input',
                    markerfacecolor='#3498db', markeredgecolor='white', markeredgewidth=1.5)
        if msg_stats.get('inter_agent_output', {}).get('values'):
            ax2.plot(run_ids, msg_stats['inter_agent_output']['values'], 's-', color='#e74c3c',
                    linewidth=2, markersize=10, label='Inter-Agent Output',
                    markerfacecolor='#e74c3c', markeredgecolor='white', markeredgewidth=1.5)
        if msg_stats.get('agent_llm_input', {}).get('values'):
            ax2.plot(run_ids, msg_stats['agent_llm_input']['values'], '^-', color='#2ecc71',
                    linewidth=2, markersize=10, label='Agent-LLM Input',
                    markerfacecolor='#2ecc71', markeredgecolor='white', markeredgewidth=1.5)
        if msg_stats.get('agent_llm_output', {}).get('values'):
            ax2.plot(run_ids, msg_stats['agent_llm_output']['values'], 'd-', color='#f39c12',
                    linewidth=2, markersize=10, label='Agent-LLM Output',
                    markerfacecolor='#f39c12', markeredgecolor='white', markeredgewidth=1.5)
        ax2.set_xlabel('Run ID', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Total Message Size (KB)', fontsize=12, fontweight='bold')
        ax2.set_title('Message Size Trend Across Runs', fontsize=14, fontweight='bold')
        ax2.legend(loc='best', fontsize=9)
        ax2.grid(alpha=0.3, linestyle='--')
        ax2.set_xticks(run_ids)

        # Chart 3: Per-agent message size totals (box plots)
        ax3 = axes[0, 2]
        if msg_stats.get('per_agent_totals'):
            agent_totals = msg_stats['per_agent_totals']
            if len(agent_totals) > 0:
                # Get top agents by average total
                agent_avgs = {agent: np.mean(totals) for agent, totals in agent_totals.items()}
                top_agents = sorted(agent_avgs.items(), key=lambda x: x[1], reverse=True)[:10]

                if top_agents:
                    box_data = [agent_totals[agent] for agent, _ in top_agents]
                    box_labels = [agent for agent, _ in top_agents]
                    colors_list = plt.cm.Set3(np.linspace(0, 1, len(box_labels)))

                    bp = ax3.boxplot(box_data, labels=box_labels, patch_artist=True,
                                   widths=0.6, showmeans=True, meanline=True)
                    for patch, color in zip(bp['boxes'], colors_list):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.7)
                    ax3.set_ylabel('Total Message Size (KB)', fontsize=10, fontweight='bold')
                    ax3.set_title('Per-Agent Message Size (Top 10)', fontsize=12, fontweight='bold')
                    ax3.set_xticklabels(box_labels, rotation=45, ha='right', fontsize=8)
                    ax3.grid(axis='y', alpha=0.3)

        # Chart 4: Per agent-pair message size totals (box plots)
        ax4 = axes[1, 0]
        if msg_stats.get('per_pair_totals'):
            pair_totals = msg_stats['per_pair_totals']
            if len(pair_totals) > 0:
                # Get top pairs by average total
                pair_avgs = {pair: np.mean(totals) for pair, totals in pair_totals.items()}
                top_pairs = sorted(pair_avgs.items(), key=lambda x: x[1], reverse=True)[:15]  # Show more pairs

                if top_pairs:
                    box_data = [pair_totals[pair] for pair, _ in top_pairs]
                    box_labels = [f'{src}→{tgt}' for (src, tgt), _ in top_pairs]
                    colors_list = plt.cm.Pastel1(np.linspace(0, 1, len(box_labels)))

                    bp = ax4.boxplot(box_data, labels=box_labels, patch_artist=True,
                                   widths=0.6, showmeans=True, meanline=True)
                    for patch, color in zip(bp['boxes'], colors_list):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.7)
                    ax4.set_ylabel('Total Message Size (KB)', fontsize=10, fontweight='bold')
                    ax4.set_title('Per Agent-Pair Message Size (Top 15)', fontsize=12, fontweight='bold')
                    ax4.set_xticklabels(box_labels, rotation=45, ha='right', fontsize=7)
                    ax4.grid(axis='y', alpha=0.3)
                else:
                    ax4.axis('off')
                    ax4.text(0.5, 0.5, 'No agent-pair message size data', ha='center', va='center', fontsize=10)
            else:
                ax4.axis('off')
                ax4.text(0.5, 0.5, 'No agent-pair message size data', ha='center', va='center', fontsize=10)
        else:
            ax4.axis('off')
            ax4.text(0.5, 0.5, 'No agent-pair message size data', ha='center', va='center', fontsize=10)

        # Chart 5: Per agent-LLM message size totals (box plots)
        ax5 = axes[1, 1]
        if msg_stats.get('per_agent_llm_totals'):
            agent_llm_totals = msg_stats['per_agent_llm_totals']
            if len(agent_llm_totals) > 0:
                # Get top agents by average total
                agent_llm_avgs = {agent: np.mean(totals) for agent, totals in agent_llm_totals.items()}
                top_agents_llm = sorted(agent_llm_avgs.items(), key=lambda x: x[1], reverse=True)[:10]

                if top_agents_llm:
                    box_data = [agent_llm_totals[agent] for agent, _ in top_agents_llm]
                    box_labels = [agent for agent, _ in top_agents_llm]
                    colors_list = plt.cm.Set2(np.linspace(0, 1, len(box_labels)))

                    bp = ax5.boxplot(box_data, labels=box_labels, patch_artist=True,
                                   widths=0.6, showmeans=True, meanline=True)
                    for patch, color in zip(bp['boxes'], colors_list):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.7)
                    ax5.set_ylabel('Total Message Size (KB)', fontsize=10, fontweight='bold')
                    ax5.set_title('Per Agent-LLM Message Size (Top 10)', fontsize=12, fontweight='bold')
                    ax5.set_xticklabels(box_labels, rotation=45, ha='right', fontsize=8)
                    ax5.grid(axis='y', alpha=0.3)

        # Chart 6: Statistics table
        ax6 = axes[1, 2]
        ax6.axis('off')
        table_data = [['Metric', 'Mean ± Std (KB)', 'Min', 'Max', 'p95']]

        if msg_stats.get('inter_agent_input'):
            iai = msg_stats['inter_agent_input']
            table_data.append([
                'Inter-Agent Input',
                f'{iai["mean"]:.2f} ± {iai["std"]:.2f}',
                f'{iai["min"]:.2f}',
                f'{iai["max"]:.2f}',
                f'{iai["p95"]:.2f}'
            ])
        if msg_stats.get('inter_agent_output'):
            iao = msg_stats['inter_agent_output']
            table_data.append([
                'Inter-Agent Output',
                f'{iao["mean"]:.2f} ± {iao["std"]:.2f}',
                f'{iao["min"]:.2f}',
                f'{iao["max"]:.2f}',
                f'{iao["p95"]:.2f}'
            ])
        if msg_stats.get('agent_llm_input'):
            ali = msg_stats['agent_llm_input']
            table_data.append([
                'Agent-LLM Input',
                f'{ali["mean"]:.2f} ± {ali["std"]:.2f}',
                f'{ali["min"]:.2f}',
                f'{ali["max"]:.2f}',
                f'{ali["p95"]:.2f}'
            ])
        if msg_stats.get('agent_llm_output'):
            alo = msg_stats['agent_llm_output']
            table_data.append([
                'Agent-LLM Output',
                f'{alo["mean"]:.2f} ± {alo["std"]:.2f}',
                f'{alo["min"]:.2f}',
                f'{alo["max"]:.2f}',
                f'{alo["p95"]:.2f}'
            ])

        if len(table_data) > 1:
            table = ax6.table(cellText=table_data[1:], colLabels=table_data[0],
                            cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
            table.auto_set_font_size(False)
            table.set_fontsize(9)
            table.scale(1, 2)
            ax6.set_title('Message Size Statistics Across Runs', fontsize=14, fontweight='bold', pad=20)

        plt.tight_layout()
        plt.savefig(str(Path(output_dir) / "4_message_sizes_per_run.pdf"), dpi=300, bbox_inches='tight')
        print(f"✓ Per-run message size chart saved to {Path(output_dir) / '4_message_sizes_per_run.pdf'}")
        plt.close()

    # 5. Generate statistics report
    report_file = Path(output_dir) / "statistics_report.txt"
    with open(report_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("Per-Run Analysis Statistics Report\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Number of runs analyzed: {n_runs}\n\n")

        if 'e2e_delay' in stats:
            e2e = stats['e2e_delay']
            f.write("E2E Delay Statistics:\n")
            f.write(f"  Mean ± Std: {e2e['mean']:.2f} ± {e2e['std']:.2f} ms\n")
            f.write(f"  Min: {e2e['min']:.2f} ms, Max: {e2e['max']:.2f} ms\n")
            f.write(f"  Percentiles: p50={e2e['p50']:.2f} ms, p95={e2e['p95']:.2f} ms, p99={e2e['p99']:.2f} ms\n")
            f.write(f"  Coefficient of Variation (CV): {e2e['cv']:.3f} ({e2e['cv']*100:.1f}%)\n")
            f.write(f"  Number of runs: {e2e['n']}\n\n")

        if 'tokens' in stats:
            tok = stats['tokens']
            f.write("Token Consumption Statistics:\n")
            f.write(f"  Total across all runs: {tok['total_tokens']:,} tokens\n")
            f.write(f"    - Prompt: {tok['total_prompt']:,} tokens\n")
            f.write(f"    - Completion: {tok['total_completion']:,} tokens\n")
            f.write(f"  Mean per run: {tok['mean_per_run']:.0f} tokens\n")
            f.write(f"  Std per run: {tok['std_per_run']:.0f} tokens\n\n")

        if 'agent_llm_delay' in stats:
            llm = stats['agent_llm_delay']
            f.write("Agent-LLM Delay Statistics:\n")
            f.write(f"  Mean ± Std: {llm['mean']:.2f} ± {llm['std']:.2f} ms\n")
            f.write(f"  Min: {llm['min']:.2f} ms, Max: {llm['max']:.2f} ms\n")
            f.write(f"  p95: {llm['p95']:.2f} ms\n")
            f.write(f"  Total observations: {llm['n']}\n\n")

        if 'inter_agent_delay' in stats:
            inter = stats['inter_agent_delay']
            f.write("Inter-Agent Delay Statistics:\n")
            f.write(f"  Mean ± Std: {inter['mean']:.2f} ± {inter['std']:.2f} ms\n")
            f.write(f"  Min: {inter['min']:.2f} ms, Max: {inter['max']:.2f} ms\n")
            f.write(f"  p95: {inter['p95']:.2f} ms\n")
            f.write(f"  Total observations: {inter['n']}\n\n")

        if 'cpu_usage' in stats:
            cpu = stats['cpu_usage']
            f.write("CPU Usage Statistics (Process-Level):\n")
            f.write("  Mean CPU Usage per Run:\n")
            f.write(f"    Mean ± Std: {cpu['mean']['mean']:.2f} ± {cpu['mean']['std']:.2f}%\n")
            f.write(f"    Min: {cpu['mean']['min']:.2f}%, Max: {cpu['mean']['max']:.2f}%\n")
            f.write(f"    p50: {cpu['mean']['p50']:.2f}%, p95: {cpu['mean']['p95']:.2f}%\n")
            f.write("  Peak CPU Usage per Run:\n")
            f.write(f"    Mean ± Std: {cpu['peak']['mean']:.2f} ± {cpu['peak']['std']:.2f}%\n")
            f.write(f"    Min: {cpu['peak']['min']:.2f}%, Max: {cpu['peak']['max']:.2f}%\n")
            f.write(f"    p50: {cpu['peak']['p50']:.2f}%, p95: {cpu['peak']['p95']:.2f}%\n")
            f.write(f"  Number of runs: {cpu['mean']['n']}\n\n")

        if 'memory_usage' in stats:
            mem = stats['memory_usage']
            f.write("Memory Usage Statistics (Process-Level):\n")
            f.write("  Mean Memory Usage per Run:\n")
            f.write(f"    Mean ± Std: {mem['mean']['mean']:.2f} ± {mem['mean']['std']:.2f} MB\n")
            f.write(f"    Min: {mem['mean']['min']:.2f} MB, Max: {mem['mean']['max']:.2f} MB\n")
            f.write(f"    p50: {mem['mean']['p50']:.2f} MB, p95: {mem['mean']['p95']:.2f} MB\n")
            f.write("  Peak Memory Usage per Run:\n")
            f.write(f"    Mean ± Std: {mem['peak']['mean']:.2f} ± {mem['peak']['std']:.2f} MB\n")
            f.write(f"    Min: {mem['peak']['min']:.2f} MB, Max: {mem['peak']['max']:.2f} MB\n")
            f.write(f"    p50: {mem['peak']['p50']:.2f} MB, p95: {mem['peak']['p95']:.2f} MB\n")
            f.write(f"  Number of runs: {mem['mean']['n']}\n\n")

        # Call graph similarity statistics
        if 'call_graph_similarity' in stats:
            cg_stats = stats['call_graph_similarity']
            f.write("Call Graph Similarity Statistics:\n")
            f.write("=" * 60 + "\n")

            # Multi-graph metrics (more comprehensive)
            f.write("Multi-Graph Similarity Metrics:\n")
            f.write(f"  Overall Consistency: {cg_stats['overall_consistency']:.4f}\n")
            f.write(f"    (Intersection of all graphs / Union of all graphs)\n")
            f.write(f"  Core Edge Ratio: {cg_stats['core_edge_ratio']:.4f}\n")
            f.write(f"    (Edges appearing in all runs / Total unique edges)\n")
            f.write(f"  Core Edges: {len(cg_stats['core_edges'])} edges\n")
            f.write(f"    {cg_stats['core_edges']}\n")
            f.write(f"  Total Unique Edges: {cg_stats['total_unique_edges']} edges\n")
            f.write(f"  Mean Pairwise Similarity: {cg_stats['mean_pairwise_similarity']:.4f}\n")
            f.write(f"    (Average of all pairwise Jaccard similarities)\n")
            f.write(f"  Stability Index: {cg_stats['stability_index']:.4f}\n")
            f.write(f"    (Std dev of pairwise similarities, lower = more stable)\n\n")

            # Consecutive pairwise (for reference)
            f.write("Consecutive Pairwise Similarities (for reference):\n")
            f.write(f"  Mean: {cg_stats['jaccard_mean']:.4f}\n")
            f.write(f"  Min: {cg_stats['jaccard_min']:.4f}\n")
            f.write(f"  Max: {cg_stats['jaccard_max']:.4f}\n")
            for i, sim in enumerate(cg_stats['jaccard_similarities']):
                edges1 = runs[i]['call_graph']
                edges2 = runs[i + 1]['call_graph']
                intersection = len(edges1 & edges2)
                union = len(edges1 | edges2)
                f.write(f"  Run {i+1} vs Run {i+2}: {sim:.4f} ({intersection} common, {union} total)\n")
            f.write("\n")

        # Call sequence similarity statistics
        if 'call_sequence_similarity' in stats:
            f.write("\nCall Sequence Similarity Statistics (LCS - Sequence Order):\n")
            lcs_stats = stats['call_sequence_similarity']
            f.write(f"  Mean LCS similarity: {lcs_stats['lcs_mean']:.4f}\n")
            f.write(f"  Min LCS similarity: {lcs_stats['lcs_min']:.4f}\n")
            f.write(f"  Max LCS similarity: {lcs_stats['lcs_max']:.4f}\n")
            for i, sim in enumerate(lcs_stats['lcs_similarities']):
                seq1 = runs[i].get('call_sequence', [])
                seq2 = runs[i + 1].get('call_sequence', [])
                f.write(f"  Run {i+1} vs Run {i+2}: {sim:.4f} (seq1 length: {len(seq1)}, seq2 length: {len(seq2)})\n")
            f.write("\n")

        if 'message_sizes' in stats:
            msg = stats['message_sizes']
            f.write("Message Size Statistics:\n")
            if 'inter_agent_input' in msg:
                f.write("  Inter-Agent Input (Total per Run):\n")
                f.write(f"    Mean ± Std: {msg['inter_agent_input']['mean']:.2f} ± {msg['inter_agent_input']['std']:.2f} KB\n")
                f.write(f"    Min: {msg['inter_agent_input']['min']:.2f} KB, Max: {msg['inter_agent_input']['max']:.2f} KB\n")
                f.write(f"    p50: {msg['inter_agent_input']['p50']:.2f} KB, p95: {msg['inter_agent_input']['p95']:.2f} KB\n")
            if 'inter_agent_output' in msg:
                f.write("  Inter-Agent Output (Total per Run):\n")
                f.write(f"    Mean ± Std: {msg['inter_agent_output']['mean']:.2f} ± {msg['inter_agent_output']['std']:.2f} KB\n")
                f.write(f"    Min: {msg['inter_agent_output']['min']:.2f} KB, Max: {msg['inter_agent_output']['max']:.2f} KB\n")
                f.write(f"    p50: {msg['inter_agent_output']['p50']:.2f} KB, p95: {msg['inter_agent_output']['p95']:.2f} KB\n")
            if 'agent_llm_input' in msg:
                f.write("  Agent-LLM Input (Total per Run):\n")
                f.write(f"    Mean ± Std: {msg['agent_llm_input']['mean']:.2f} ± {msg['agent_llm_input']['std']:.2f} KB\n")
                f.write(f"    Min: {msg['agent_llm_input']['min']:.2f} KB, Max: {msg['agent_llm_input']['max']:.2f} KB\n")
                f.write(f"    p50: {msg['agent_llm_input']['p50']:.2f} KB, p95: {msg['agent_llm_input']['p95']:.2f} KB\n")
            if 'agent_llm_output' in msg:
                f.write("  Agent-LLM Output (Total per Run):\n")
                f.write(f"    Mean ± Std: {msg['agent_llm_output']['mean']:.2f} ± {msg['agent_llm_output']['std']:.2f} KB\n")
                f.write(f"    Min: {msg['agent_llm_output']['min']:.2f} KB, Max: {msg['agent_llm_output']['max']:.2f} KB\n")
                f.write(f"    p50: {msg['agent_llm_output']['p50']:.2f} KB, p95: {msg['agent_llm_output']['p95']:.2f} KB\n")
            f.write("\n")

    print(f"✓ Statistics report saved to {report_file}")

    # 6. Call Graph Visualization and Similarity Analysis
    if len(runs) > 0:
        # Extract call graphs for each run
        call_graphs = []
        call_sequences = []
        for run in runs:
            edges = run.get('call_graph', set())
            call_graphs.append(edges)
            call_sequences.append(run.get('call_sequence', []))

        # Normalize agent names to avoid duplicates from minor formatting differences
        def _normalize_agent(agent_name: str) -> str:
            if not agent_name:
                return ''
            name = agent_name.lower().strip()
            name = name.replace('_agent', '').replace('-agent', '').replace(' agent', '')
            name = name.replace('_', '-').replace(' ', '-')
            name = re.sub(r'-+', '-', name).strip('-')
            return name

        def _normalize_edge_set(edges: set) -> set:
            normalized = set()
            for src, tgt in edges:
                ns, nt = _normalize_agent(src), _normalize_agent(tgt)
                if ns and nt and ns != nt:
                    normalized.add((ns, nt))
            return normalized

        def _normalize_sequence(seq: List[tuple]) -> List[tuple]:
            normalized = []
            for src, tgt in seq:
                ns, nt = _normalize_agent(src), _normalize_agent(tgt)
                if ns and nt and ns != nt:
                    normalized.append((ns, nt))
            return normalized

        call_graphs = [_normalize_edge_set(edges) for edges in call_graphs]
        call_sequences = [_normalize_sequence(seq) for seq in call_sequences]

        # Generate call graph visualization even if there's only one run (show the call graph)
        # Similarity analysis requires at least 2 runs
        if call_graphs:
            n_runs = len(call_graphs)
            has_similarity = 'call_graph_similarity' in stats and n_runs > 1
            cg_stats = stats.get('call_graph_similarity', {})
            core_edges_normalized = set()
            if cg_stats:
                for src, tgt in cg_stats.get('core_edges', []):
                    ns = _normalize_agent(src)
                    nt = _normalize_agent(tgt)
                    if ns and nt and ns != nt:
                        core_edges_normalized.add((ns, nt))
            max_sequence_len = max((len(seq) for seq in call_sequences), default=0)

            # Create visualization with multiple subplots
            # Layout: call graphs (top row) + similarity matrices (middle, side by side) + metrics (bottom)
            from matplotlib.gridspec import GridSpec
            from matplotlib import cm
            fig = plt.figure(figsize=(24, 16))
            # Use a simpler layout: 3 rows if we have similarity, 1 row if single run
            if has_similarity:
                # Row 0: n_runs call graphs
                # Row 1: 2 matrices side by side (each matrix spans equal space)
                # Row 2: metrics table (full width)
                # Use fixed column layout: n_runs columns for graphs, then 2 equal sections for matrices
                # Total: n_runs + 4 columns (2 columns per matrix section)
                n_cols = n_runs + 4
                gs = GridSpec(3, n_cols, figure=fig, hspace=0.3, wspace=0.3, height_ratios=[1, 1, 0.8])
            else:
                # Single run: just show call graphs
                n_cols = min(n_runs, 4)  # Limit to 4 columns for single run
                gs = GridSpec(1, n_cols, figure=fig, hspace=0.3, wspace=0.3)
            # Disable grid lines in GridSpec
            fig.patch.set_visible(False)
            order_cmap = cm.get_cmap('plasma') if max_sequence_len > 0 else None
            if max_sequence_len > 0:
                norm_max = max_sequence_len - 1 if max_sequence_len > 1 else 1
                order_norm = plt.Normalize(vmin=0, vmax=norm_max)
            else:
                order_norm = None
            order_colorbar_added = False

            # Plot call graphs for each run
            # Extract agents from edges first
            all_agents = set()
            for edges in call_graphs:
                for src, tgt in edges:
                    all_agents.add(src)
                    all_agents.add(tgt)
            # Include agents from ordered call sequences (sequence can exist even if graph set is empty)
            for seq in call_sequences:
                for src, tgt in seq:
                    all_agents.add(src)
                    all_agents.add(tgt)

            # If no agents found from edges, extract from traces (for systems where agents don't call each other directly)
            if not all_agents and runs:
                from .extractors import extract_token_consumption
                # Extract agents from first run's traces (all runs should have same agents)
                first_run_traces = runs[0].get('traces', [])
                if first_run_traces:
                    _, per_agent_tokens = extract_token_consumption(first_run_traces)
                    # Normalize agent names to match call graph format
                    for agent_name in per_agent_tokens.keys():
                        normalized = agent_name.replace('_agent', '').replace('-agent', '').replace('_', '-').replace(' ', '-').lower()
                        all_agents.add(normalized)

            all_agents = sorted(list(all_agents))

            # Create node positions (circular layout)
            import math
            node_positions = {}
            n_nodes = len(all_agents)
            for i, agent in enumerate(all_agents):
                angle = 2 * math.pi * i / n_nodes if n_nodes > 0 else 0
                node_positions[agent] = (math.cos(angle), math.sin(angle))

            for i, edges in enumerate(call_graphs):
                if i < n_cols:
                    ax = fig.add_subplot(gs[0, i])
                    ax.set_aspect('equal')
                    ax.axis('off')
                    sequence = call_sequences[i] if i < len(call_sequences) else []
                    edges_to_draw = sequence if sequence else list(edges)
                    edge_occurrence_totals = defaultdict(int)
                    for pair in edges_to_draw:
                        edge_occurrence_totals[pair] += 1
                    edge_occurrence_seen = defaultdict(int)

                    # Draw edges
                    for idx, (src, tgt) in enumerate(edges_to_draw):
                        if src in node_positions and tgt in node_positions:
                            x1, y1 = node_positions[src]
                            x2, y2 = node_positions[tgt]
                            # Highlight core edges (edges in all runs) if we have similarity stats
                            is_core = (src, tgt) in core_edges_normalized if has_similarity else False
                            if sequence and order_cmap and order_norm:
                                color = order_cmap(order_norm(idx))
                                linewidth = 2.5 + (1 if is_core else 0)
                            else:
                                color = '#e74c3c' if is_core else 'gray'
                                linewidth = 3 if is_core else 2

                            # Offset duplicate edges slightly to avoid overlapping text/arrows
                            edge_occurrence_seen[(src, tgt)] += 1
                            occurrence_idx = edge_occurrence_seen[(src, tgt)]
                            total_occurrences = edge_occurrence_totals[(src, tgt)]
                            dx = x2 - x1
                            dy = y2 - y1
                            length = math.sqrt(dx**2 + dy**2)
                            offset_scale = 0.12
                            offset = 0.0
                            if length > 0 and total_occurrences > 1:
                                offset = (occurrence_idx - (total_occurrences + 1) / 2) * offset_scale
                            if length > 0:
                                perp_x, perp_y = -dy / length, dx / length
                                x1o = x1 + perp_x * offset
                                y1o = y1 + perp_y * offset
                                x2o = x2 + perp_x * offset
                                y2o = y2 + perp_y * offset
                            else:
                                x1o, y1o, x2o, y2o = x1, y1, x2, y2

                            ax.plot([x1o, x2o], [y1o, y2o], color=color, linewidth=linewidth, alpha=0.8, zorder=1)
                            # Draw arrow
                            dx_line, dy_line = x2o - x1o, y2o - y1o
                            length_line = math.sqrt(dx_line**2 + dy_line**2)
                            if length_line > 0:
                                dx_arrow, dy_arrow = dx_line / length_line * 0.15, dy_line / length_line * 0.15
                                ax.arrow(x2o - dx_arrow, y2o - dy_arrow, dx_arrow, dy_arrow, head_width=0.05, head_length=0.05,
                                       fc=color, ec=color, zorder=2)
                            # Annotate order number at midpoint when we have sequence data
                            if sequence:
                                mx, my = (x1o + x2o) / 2, (y1o + y2o) / 2
                                ax.text(mx, my, f'{idx + 1}', ha='center', va='center', fontsize=8, fontweight='bold',
                                        color='black', zorder=5,
                                        bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7))

                    # Draw nodes - show all agents if we have them, otherwise show agents from edges
                    if all_agents:
                        # Show all agents (even if no edges, they're still part of the system)
                        for agent in all_agents:
                            if agent in node_positions:
                                x, y = node_positions[agent]
                                ax.scatter([x], [y], s=500, c='#3498db', edgecolors='black', linewidths=2, zorder=3)
                                ax.text(x, y - 0.2, agent, ha='center', va='top', fontsize=8, fontweight='bold', zorder=4)
                    else:
                        # Fallback: show agents from edges only
                        for agent, (x, y) in node_positions.items():
                            in_graph = any(agent in (src, tgt) for src, tgt in edges)
                            if in_graph:
                                ax.scatter([x], [y], s=500, c='#3498db', edgecolors='black', linewidths=2, zorder=3)
                                ax.text(x, y - 0.2, agent, ha='center', va='top', fontsize=8, fontweight='bold', zorder=4)

                    ax.set_xlim(-1.5, 1.5)
                    ax.set_ylim(-1.5, 1.5)
                    call_count_label = f"{len(sequence)} calls" if sequence else f"{len(edges)} edges"
                    ax.set_title(f'Run {i+1}\n({call_count_label})', fontsize=12, fontweight='bold', pad=10)

                    # Add a single colorbar showing call order (shared scale across runs)
                    if sequence and order_cmap and order_norm and not order_colorbar_added:
                        sm = plt.cm.ScalarMappable(cmap=order_cmap, norm=order_norm)
                        sm.set_array([])
                        cbar = plt.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
                        cbar.set_label('Call order (1 = earliest)', fontsize=10, fontweight='bold')
                        if max_sequence_len > 0:
                            if max_sequence_len <= 10:
                                ticks = list(range(max_sequence_len))
                            else:
                                step = max(1, max_sequence_len // 5)
                                ticks = list(range(0, max_sequence_len, step))
                            cbar.set_ticks(ticks)
                            cbar.set_ticklabels([str(t + 1) for t in ticks])
                        order_colorbar_added = True

            # Row 2: Similarity Matrices (Jaccard and LCS side by side) - only if we have similarity stats and multiple runs
            if has_similarity and 'similarity_matrix' in cg_stats and len(cg_stats['similarity_matrix']) > 1:
                # Calculate column positions: matrices start after call graph columns
                # Each matrix spans 2 columns for proper spacing
                matrix_start = n_runs
                matrix_mid = matrix_start + 2

                # Jaccard Similarity Matrix (Edge Set, No Order) - Left side
                ax_jaccard = fig.add_subplot(gs[1, matrix_start:matrix_mid])
                ax_jaccard.grid(False)  # Explicitly disable grid
                jaccard_matrix = np.array(cg_stats['similarity_matrix'])
                # Directly plot the matrix without extent - let imshow handle it naturally
                # Then adjust ticks and labels to match the data
                im1 = ax_jaccard.imshow(jaccard_matrix, cmap='RdYlGn', vmin=0, vmax=1,
                                       aspect='equal', interpolation='nearest', origin='upper')

                # Set ticks to match data dimensions (3x3) - only show ticks at data points
                ax_jaccard.set_xticks(range(n_runs))
                ax_jaccard.set_yticks(range(n_runs))
                ax_jaccard.set_xticklabels([f'Run {i+1}' for i in range(n_runs)], fontsize=9)
                ax_jaccard.set_yticklabels([f'Run {i+1}' for i in range(n_runs)], fontsize=9)

                # Add text annotations at correct data positions
                for i in range(n_runs):
                    for j in range(n_runs):
                        ax_jaccard.text(j, i, f'{jaccard_matrix[i, j]:.2f}',
                                       ha="center", va="center", color="black", fontweight='bold', fontsize=9)

                ax_jaccard.set_xlabel('Run', fontsize=11, fontweight='bold')
                ax_jaccard.set_ylabel('Run', fontsize=11, fontweight='bold')
                ax_jaccard.set_title('Jaccard Similarity (Edge Set, No Order)',
                                  fontsize=12, fontweight='bold', pad=10)

                # Add colorbar
                cbar1 = plt.colorbar(im1, ax=ax_jaccard, fraction=0.046, pad=0.04)
                cbar1.set_label('Similarity', fontsize=10, fontweight='bold')
                cbar1.ax.tick_params(labelsize=8)

                # LCS Similarity Matrix (Sequence Order) - Right side
                if 'call_sequence_similarity' in stats and 'lcs_matrix' in stats['call_sequence_similarity']:
                    ax_lcs = fig.add_subplot(gs[1, matrix_mid:])
                    ax_lcs.grid(False)  # Explicitly disable grid
                    lcs_matrix = np.array(stats['call_sequence_similarity']['lcs_matrix'])
                    # Directly plot the matrix without extent - let imshow handle it naturally
                    im2 = ax_lcs.imshow(lcs_matrix, cmap='RdYlGn', vmin=0, vmax=1,
                                       aspect='equal', interpolation='nearest', origin='upper')

                    # Set ticks to match data dimensions (3x3) - only show ticks at data points
                    ax_lcs.set_xticks(range(n_runs))
                    ax_lcs.set_yticks(range(n_runs))
                    ax_lcs.set_xticklabels([f'Run {i+1}' for i in range(n_runs)], fontsize=9)
                    ax_lcs.set_yticklabels([f'Run {i+1}' for i in range(n_runs)], fontsize=9)

                    # Add text annotations at correct data positions
                    for i in range(n_runs):
                        for j in range(n_runs):
                            ax_lcs.text(j, i, f'{lcs_matrix[i, j]:.2f}',
                                       ha="center", va="center", color="black", fontweight='bold', fontsize=9)

                    ax_lcs.set_xlabel('Run', fontsize=11, fontweight='bold')
                    ax_lcs.set_ylabel('Run', fontsize=11, fontweight='bold')
                    ax_lcs.set_title('LCS Similarity (Sequence Order)',
                                  fontsize=12, fontweight='bold', pad=10)

                    # Add colorbar
                    cbar2 = plt.colorbar(im2, ax=ax_lcs, fraction=0.046, pad=0.04)
                    cbar2.set_label('Similarity', fontsize=10, fontweight='bold')
                    cbar2.ax.tick_params(labelsize=8)

            # Row 3: Multi-Graph Metrics Summary (only if we have similarity stats)
            if has_similarity and n_runs > 1:
                ax_metrics = fig.add_subplot(gs[2, :])
                ax_metrics.axis('off')

                # Create metrics summary table
                metrics_data = [
                    ['Metric', 'Value', 'Interpretation'],
                    ['Overall Consistency (Jaccard)', f"{cg_stats.get('overall_consistency', 0):.4f}",
                     f"Core edges / Total unique edges ({len(cg_stats.get('core_edges', []))} / {cg_stats.get('total_unique_edges', 0)})"],
                    ['Mean Pairwise Jaccard', f"{cg_stats.get('mean_pairwise_similarity', 0):.4f}",
                     f"Average edge set similarity (no order)"],
                ]

                # Add LCS similarity if available
                if 'call_sequence_similarity' in stats:
                    lcs_stats = stats['call_sequence_similarity']
                    metrics_data.append(['Mean Pairwise LCS', f"{lcs_stats.get('mean_pairwise_lcs', 0):.4f}",
                                       f"Average sequence similarity (with order)"])
                    metrics_data.append(['LCS Stability Index', f"{lcs_stats.get('lcs_stability_index', 0):.4f}",
                                       f"Std dev of LCS similarities (lower = more stable)"])

                metrics_data.extend([
                    ['Jaccard Stability Index', f"{cg_stats.get('stability_index', 0):.4f}",
                     f"Std dev of Jaccard similarities (lower = more stable)"],
                    ['Core Edges', f"{len(cg_stats.get('core_edges', []))}",
                     f"Edges appearing in all {n_runs} runs"],
                ])

                # Add core edges list (truncated if too long)
                core_edges = cg_stats.get('core_edges', [])
                core_edges_str = str(core_edges)
                if len(core_edges_str) > 80:
                    core_edges_str = core_edges_str[:77] + "..."
                metrics_data.append(['Core Edge List', core_edges_str, 'Stable call patterns'])

                table = ax_metrics.table(cellText=metrics_data[1:], colLabels=metrics_data[0],
                                        cellLoc='left', loc='center', bbox=[0, 0, 1, 1])
                table.auto_set_font_size(False)
                table.set_fontsize(10)
                table.scale(1, 2.5)

                # Style header row
                for i in range(3):
                    table[(0, i)].set_facecolor('#3498db')
                    table[(0, i)].set_text_props(weight='bold', color='white')

                ax_metrics.set_title('Multi-Graph Similarity Metrics Summary',
                                   fontsize=14, fontweight='bold', pad=20)
            elif n_runs == 1:
                # Single run: add a note in an empty subplot if available
                if n_cols > n_runs:
                    try:
                        ax_note = fig.add_subplot(gs[0, n_runs])
                        ax_note.axis('off')
                        ax_note.text(0.5, 0.5, f'Note: Similarity analysis requires\nat least 2 runs. Current: {n_runs} run(s).',
                                   ha='center', va='center', fontsize=10, style='italic',
                                   bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
                    except:
                        pass  # If subplot creation fails, just skip the note

            plt.tight_layout()
            plt.savefig(str(Path(output_dir) / "5_call_graph_similarity.pdf"), dpi=300, bbox_inches='tight')
            print(f"✓ Call graph similarity chart saved to {Path(output_dir) / '5_call_graph_similarity.pdf'}")
            plt.close()
