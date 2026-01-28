"""Visualization utilities for OpenTelemetry metrics data."""

import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict
from typing import Dict, List, Tuple

# Set style
plt.style.use('seaborn-v0_8-darkgrid')
plt.rcParams['figure.figsize'] = (14, 10)
plt.rcParams['font.size'] = 10


def plot_token_consumption(total_tokens: Dict[str, int], per_agent_tokens: Dict[str, Dict[str, int]], output_file: str):
    """Plot token consumption charts."""
    fig = plt.figure(figsize=(16, 10))

    # Chart 1: Total token consumption (stacked bar)
    ax1 = plt.subplot(2, 2, 1)
    categories = ['Prompt Tokens', 'Completion Tokens']
    values = [total_tokens['prompt'], total_tokens['completion']]
    colors = ['#3498db', '#e74c3c']

    ax1.bar(categories, values, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    ax1.set_ylabel('Token Count', fontsize=12, fontweight='bold')
    ax1.set_title('Total Token Consumption (MAS)', fontsize=14, fontweight='bold')
    ax1.grid(axis='y', alpha=0.3)

    # Add value labels
    for i, v in enumerate(values):
        if v > 0:
            ax1.text(i, v + max(values) * 0.01, f'{v:,}', ha='center', va='bottom', fontweight='bold')

    # Chart 2: Per-agent token consumption (stacked)
    ax2 = plt.subplot(2, 2, 2)
    if per_agent_tokens:
        agents = list(per_agent_tokens.keys())
        prompt_vals = [per_agent_tokens[agent]['prompt'] for agent in agents]
        completion_vals = [per_agent_tokens[agent]['completion'] for agent in agents]

        x = np.arange(len(agents))
        width = 0.6

        ax2.bar(x, prompt_vals, width, label='Prompt Tokens', color='#3498db', alpha=0.8, edgecolor='black')
        ax2.bar(x, completion_vals, width, bottom=prompt_vals, label='Completion Tokens',
                color='#e74c3c', alpha=0.8, edgecolor='black')

        ax2.set_xlabel('Agent', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax2.set_title('Token Consumption per Agent (Stacked)', fontsize=14, fontweight='bold')
        ax2.set_xticks(x)
        ax2.set_xticklabels(agents, rotation=45, ha='right')
        ax2.legend()
        ax2.grid(axis='y', alpha=0.3)

        # Add total labels
        for i, agent in enumerate(agents):
            total = prompt_vals[i] + completion_vals[i]
            if total > 0:
                ax2.text(i, total + max(prompt_vals[i] + completion_vals[i] for i in range(len(agents))) * 0.01,
                        f'{total:,}', ha='center', va='bottom', fontweight='bold', fontsize=9)

    # Chart 3: Per-agent token consumption (side-by-side)
    ax3 = plt.subplot(2, 2, 3)
    if per_agent_tokens:
        agents = list(per_agent_tokens.keys())
        prompt_vals = [per_agent_tokens[agent]['prompt'] for agent in agents]
        completion_vals = [per_agent_tokens[agent]['completion'] for agent in agents]

        x = np.arange(len(agents))
        width = 0.35

        ax3.bar(x - width/2, prompt_vals, width, label='Prompt Tokens', color='#3498db', alpha=0.8, edgecolor='black')
        ax3.bar(x + width/2, completion_vals, width, label='Completion Tokens', color='#e74c3c', alpha=0.8, edgecolor='black')

        ax3.set_xlabel('Agent', fontsize=12, fontweight='bold')
        ax3.set_ylabel('Token Count', fontsize=12, fontweight='bold')
        ax3.set_title('Token Consumption per Agent (Side-by-Side)', fontsize=14, fontweight='bold')
        ax3.set_xticks(x)
        ax3.set_xticklabels(agents, rotation=45, ha='right')
        ax3.legend()
        ax3.grid(axis='y', alpha=0.3)

    # Chart 4: Token statistics table
    ax4 = plt.subplot(2, 2, 4)
    ax4.axis('off')

    stats_data = [
        ['Metric', 'Value'],
        ['Total Prompt Tokens', f'{total_tokens["prompt"]:,}'],
        ['Total Completion Tokens', f'{total_tokens["completion"]:,}'],
        ['Total Tokens', f'{total_tokens["total"]:,}'],
    ]

    if per_agent_tokens:
        for agent in sorted(per_agent_tokens.keys()):
            tokens = per_agent_tokens[agent]
            stats_data.append([f'{agent} Prompt', f'{tokens["prompt"]:,}'])
            stats_data.append([f'{agent} Completion', f'{tokens["completion"]:,}'])
            stats_data.append([f'{agent} Total', f'{tokens["total"]:,}'])

    table = ax4.table(cellText=stats_data[1:], colLabels=stats_data[0],
                     cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    ax4.set_title('Token Consumption Statistics', fontsize=14, fontweight='bold', pad=20)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Token consumption chart saved to {output_file}")
    plt.close()


def plot_delay_breakdown(delays: Dict[str, List[float]], per_agent_delays: Dict[str, List[float]],
                         inter_agent_delays: Dict[Tuple[str, str], List[float]],
                         agent_llm_delays: Dict[str, List[float]], output_file: str):
    """Plot delay breakdown and distribution charts with per-agent details."""
    fig = plt.figure(figsize=(20, 14))

    # Chart 1: Delay breakdown (stacked bar)
    ax1 = plt.subplot(3, 3, 1)

    if delays['e2e_delay']:
        # E2E delay is now the total sum of all requests
        e2e_total = delays['e2e_delay'][0] if delays['e2e_delay'] else 0

        # Calculate totals for components (sum of all individual delays)
        llm_total = sum(delays['agent_llm_delay']) if delays['agent_llm_delay'] else 0
        inter_total = sum(delays['inter_agent_delay']) if delays['inter_agent_delay'] else 0
        proc_total = sum(delays['agent_processing_delay']) if delays['agent_processing_delay'] else 0

        categories = ['Total E2E Delay\n(All Requests)']
        llm_values = [llm_total]
        inter_values = [inter_total]
        proc_values = [proc_total]
        other_values = [max(0, e2e_total - llm_total - inter_total - proc_total)]

        x = np.arange(len(categories))
        width = 0.6

        p1 = ax1.bar(x, llm_values, width, label='Agent-LLM Delay', color='#e74c3c', alpha=0.8, edgecolor='black')
        p2 = ax1.bar(x, inter_values, width, bottom=llm_values, label='Inter-Agent Delay', color='#3498db', alpha=0.8, edgecolor='black')
        p3 = ax1.bar(x, proc_values, width, bottom=np.array(llm_values) + np.array(inter_values),
                     label='Processing Delay', color='#2ecc71', alpha=0.8, edgecolor='black')
        p4 = ax1.bar(x, other_values, width,
                     bottom=np.array(llm_values) + np.array(inter_values) + np.array(proc_values),
                     label='Other', color='#95a5a6', alpha=0.8, edgecolor='black')

        ax1.set_ylabel('Delay (ms)', fontsize=12, fontweight='bold')
        ax1.set_title('E2E Delay Breakdown', fontsize=14, fontweight='bold')
        ax1.set_xticks(x)
        ax1.set_xticklabels(categories)
        ax1.legend()
        ax1.grid(axis='y', alpha=0.3)

        # Add total value label
        ax1.text(x[0], e2e_total + max(e2e_total * 0.05, 10), f'Total: {e2e_total/1000:.2f}s',
                ha='center', va='bottom', fontweight='bold')

    # Chart 2: Component delay statistics (box plot or bar chart)
    ax2 = plt.subplot(3, 3, 2)

    # Show component delays as box plots to avoid overlap
    has_data = False
    components = []
    delay_data = []
    colors_list = []

    if delays['agent_llm_delay']:
        components.append('Agent-LLM')
        delay_data.append(delays['agent_llm_delay'])
        colors_list.append('#e74c3c')
        has_data = True

    if delays['inter_agent_delay']:
        components.append('Inter-Agent')
        delay_data.append(delays['inter_agent_delay'])
        colors_list.append('#3498db')
        has_data = True

    if delays['agent_processing_delay']:
        components.append('Processing')
        delay_data.append(delays['agent_processing_delay'])
        colors_list.append('#2ecc71')
        has_data = True

    if has_data:
        # Use box plot to show distribution without overlap
        bp = ax2.boxplot(delay_data, labels=components, patch_artist=True,
                        widths=0.6, showmeans=True, meanline=True)

        # Color the boxes
        for patch, color in zip(bp['boxes'], colors_list):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)

        # Style the median and mean lines
        for median in bp['medians']:
            median.set_color('black')
            median.set_linewidth(2)
        for mean in bp['means']:
            mean.set_color('red')
            mean.set_linewidth(2)
            mean.set_linestyle('--')

        ax2.set_ylabel('Delay (ms)', fontsize=12, fontweight='bold')
        ax2.set_title('Component Delay Distribution', fontsize=14, fontweight='bold')
        ax2.grid(axis='y', alpha=0.3)
        ax2.tick_params(axis='x', rotation=45)
    else:
        ax2.text(0.5, 0.5, 'No component delay data', ha='center', va='center', fontsize=12)
        ax2.set_title('Component Delay Distribution', fontsize=14, fontweight='bold')

    # Chart 3: Per-agent processing delays
    ax3 = plt.subplot(3, 3, 3)

    if per_agent_delays:
        agents = list(per_agent_delays.keys())
        avg_delays = [np.mean(per_agent_delays[agent]) for agent in agents]

        colors = plt.cm.Set3(np.linspace(0, 1, len(agents)))
        ax3.bar(agents, avg_delays, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
        ax3.set_ylabel('Average Processing Delay (ms)', fontsize=10, fontweight='bold')
        ax3.set_title('Processing Delay per Agent', fontsize=12, fontweight='bold')
        ax3.set_xticklabels(agents, rotation=45, ha='right', fontsize=8)
        ax3.grid(axis='y', alpha=0.3)

        for i, v in enumerate(avg_delays):
            if v > 0:
                ax3.text(i, v + max(avg_delays) * 0.01, f'{v:.1f}ms', ha='center', va='bottom', fontweight='bold', fontsize=8)

    # Chart 4: Inter-agent delays
    ax4 = plt.subplot(3, 3, 4)

    if inter_agent_delays:
        pairs = [f"{src}→{tgt}" for (src, tgt) in inter_agent_delays.keys()]
        avg_delays = [np.mean(inter_agent_delays[pair]) for pair in inter_agent_delays.keys()]

        if pairs:
            colors = plt.cm.Pastel1(np.linspace(0, 1, len(pairs)))
            ax4.barh(pairs, avg_delays, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
            ax4.set_xlabel('Average Delay (ms)', fontsize=10, fontweight='bold')
            ax4.set_title('Inter-Agent Communication Delays', fontsize=12, fontweight='bold')
            ax4.set_yticks(range(len(pairs)))
            ax4.set_yticklabels(pairs, fontsize=8)
            ax4.grid(axis='x', alpha=0.3)

            for i, v in enumerate(avg_delays):
                if v > 0:
                    ax4.text(v + max(avg_delays) * 0.01, i, f'{v:.1f}ms', ha='left', va='center', fontweight='bold', fontsize=8)
    else:
        ax4.axis('off')
        ax4.text(0.5, 0.5, 'No inter-agent delay data', ha='center', va='center', fontsize=10)

    # Chart 5: Detailed delay breakdown by agent and communication
    ax5 = plt.subplot(3, 3, 5)

    # Collect all detailed delay information
    breakdown_data = []

    # Agent-LLM delays per agent
    for agent_name, llm_delays in agent_llm_delays.items():
        if llm_delays:
            total_llm = sum(llm_delays)
            avg_llm = np.mean(llm_delays)
            breakdown_data.append({
                'label': f'{agent_name}\n→ LLM',
                'total': total_llm,
                'avg': avg_llm,
                'count': len(llm_delays),
                'type': 'agent_llm',
                'color': '#e74c3c'
            })

    # Inter-agent communication delays
    for (source, target), comm_delays in inter_agent_delays.items():
        if comm_delays:
            total_comm = sum(comm_delays)
            avg_comm = np.mean(comm_delays)
            breakdown_data.append({
                'label': f'{source}\n→ {target}',
                'total': total_comm,
                'avg': avg_comm,
                'count': len(comm_delays),
                'type': 'inter_agent',
                'color': '#3498db'
            })

    # Agent processing delays
    for agent_name, proc_delays in per_agent_delays.items():
        if proc_delays:
            total_proc = sum(proc_delays)
            avg_proc = np.mean(proc_delays)
            breakdown_data.append({
                'label': f'{agent_name}\nProcessing',
                'total': total_proc,
                'avg': avg_proc,
                'count': len(proc_delays),
                'type': 'processing',
                'color': '#2ecc71'
            })

    if breakdown_data:
        # Sort by total delay (descending)
        breakdown_data.sort(key=lambda x: x['total'], reverse=True)

        # Show all items
        labels = [d['label'] for d in breakdown_data]
        totals = [d['total'] for d in breakdown_data]
        colors = [d['color'] for d in breakdown_data]

        # Create horizontal bar chart
        y_pos = np.arange(len(labels))
        ax5.barh(y_pos, totals, color=colors, alpha=0.8, edgecolor='black', linewidth=1)
        ax5.set_yticks(y_pos)
        ax5.set_yticklabels(labels, fontsize=8)
        ax5.set_xlabel('Total Delay (ms)', fontsize=10, fontweight='bold')
        ax5.set_title('Detailed Delay Breakdown\n(All Components)', fontsize=12, fontweight='bold')
        ax5.grid(axis='x', alpha=0.3)

        # Add value labels
        max_total = max(totals) if totals else 0
        for i, (total, avg, count) in enumerate(zip(totals, [d['avg'] for d in breakdown_data], [d['count'] for d in breakdown_data])):
            ax5.text(total + max_total * 0.01, i, f'{total:.0f}ms\n(avg: {avg:.0f}ms, n={count})',
                    ha='left', va='center', fontsize=7, fontweight='bold')
    else:
        ax5.axis('off')
        ax5.text(0.5, 0.5, 'No detailed delay data', ha='center', va='center', fontsize=10)

    # Chart 6: Delay statistics table
    ax6 = plt.subplot(3, 3, 6)
    ax6.axis('off')

    # Chart 5: Component delays comparison
    ax5 = plt.subplot(3, 3, 5)

    if delays['e2e_delay']:
        e2e_total = delays['e2e_delay'][0]
        stats_data = [
            ['Metric', 'Value'],
            ['Total E2E Delay', f'{e2e_total/1000:.2f} s ({e2e_total:.2f} ms)'],
            ['Total E2E Delay', f'{e2e_total/60000:.2f} minutes'],
        ]

        if delays['agent_llm_delay']:
            llm_total = sum(delays['agent_llm_delay'])
            stats_data.append(['Total LLM Delay', f'{llm_total/1000:.2f} s'])
            stats_data.append(['LLM Count', f'{len(delays["agent_llm_delay"])}'])
            stats_data.append(['Avg LLM Delay', f'{np.mean(delays["agent_llm_delay"]):.2f} ms'])
        if delays['inter_agent_delay']:
            inter_total = sum(delays['inter_agent_delay'])
            stats_data.append(['Total Inter-Agent Delay', f'{inter_total/1000:.2f} s'])
            stats_data.append(['Inter-Agent Count', f'{len(delays["inter_agent_delay"])}'])
            stats_data.append(['Avg Inter-Agent Delay', f'{np.mean(delays["inter_agent_delay"]):.2f} ms'])
        if delays['agent_processing_delay']:
            proc_total = sum(delays['agent_processing_delay'])
            stats_data.append(['Total Processing Delay', f'{proc_total/1000:.2f} s'])
            stats_data.append(['Processing Count', f'{len(delays["agent_processing_delay"])}'])
            stats_data.append(['Avg Processing Delay', f'{np.mean(delays["agent_processing_delay"]):.2f} ms'])

        table = ax6.table(cellText=stats_data[1:], colLabels=stats_data[0],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.5)
        ax6.set_title('Delay Statistics', fontsize=12, fontweight='bold', pad=10)

    # Chart 7-9: Per-agent delay details
    ax7 = plt.subplot(3, 3, 7)
    ax7.axis('off')
    if per_agent_delays:
        agent_stats = []
        for agent in sorted(per_agent_delays.keys()):
            delays_list = per_agent_delays[agent]
            agent_stats.append([agent, f'{np.mean(delays_list):.2f}', f'{np.max(delays_list):.2f}'])

        table = ax7.table(cellText=agent_stats, colLabels=['Agent', 'Mean (ms)', 'Max (ms)'],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.5)
        ax7.set_title('Per-Agent Processing Delays', fontsize=12, fontweight='bold', pad=10)

    ax8 = plt.subplot(3, 3, 8)
    ax8.axis('off')
    if inter_agent_delays:
        comm_stats = []
        for (src, tgt), delays_list in sorted(inter_agent_delays.items()):
            comm_stats.append([f'{src}→{tgt}', f'{np.mean(delays_list):.2f}', f'{np.max(delays_list):.2f}'])

        table = ax8.table(cellText=comm_stats[:10], colLabels=['Communication', 'Mean (ms)', 'Max (ms)'],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(7)
        table.scale(1, 1.2)
        ax8.set_title('Inter-Agent Delays (Top 10)', fontsize=12, fontweight='bold', pad=10)

    ax9 = plt.subplot(3, 3, 9)
    ax9.axis('off')
    note_text = "Note: Processing delay is calculated as invoke_agent duration minus child spans.\n"
    note_text += "If processing delay appears small, it may indicate:\n"
    note_text += "1. Most time is spent in LLM calls (network I/O)\n"
    note_text += "2. Agent logic is lightweight (as expected)\n"
    note_text += "3. Overhead is primarily in coordination/orchestration"
    ax9.text(0.1, 0.5, note_text, fontsize=9, verticalalignment='center',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    ax9.set_title('Processing Delay Notes', fontsize=12, fontweight='bold', pad=10)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Delay breakdown chart saved to {output_file}")
    plt.close()


def plot_cpu_memory_usage(usage: Dict, output_file: str):
    """Plot CPU and memory usage charts (process-level only)."""
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 10))

    # Chart 1: Process CPU usage over time
    # Check if we have per-agent data (distributed system) or combined data (monolithic)
    has_per_agent = 'per_agent' in usage.get('cpu', {}) and usage['cpu'].get('per_agent')

    if has_per_agent and usage['cpu']['per_agent']:
        # Distributed system: plot per-agent CPU usage
        colors = plt.cm.Set3(range(len(usage['cpu']['per_agent'])))
        for idx, (agent_name, agent_data) in enumerate(usage['cpu']['per_agent'].items()):
            if agent_data:
                times, values = zip(*agent_data)
                # Normalize times to start from 0
                start_time = times[0] if times else 0
                times = [t - start_time for t in times]
                ax1.plot(times, values, label=f'{agent_name} CPU (%)', color=colors[idx], linewidth=2, alpha=0.8)

        ax1.set_xlabel('Time (seconds)', fontsize=12, fontweight='bold')
        ax1.set_ylabel('CPU Usage (%)', fontsize=12, fontweight='bold')
        ax1.set_title('Process CPU Usage Over Time (Per-Agent)', fontsize=14, fontweight='bold')
        ax1.legend(fontsize=9, loc='best')
        ax1.grid(alpha=0.3)
    elif usage['cpu']['process']:
        # Monolithic system: plot combined CPU usage
        times_proc, values_proc = zip(*usage['cpu']['process'])

        # Normalize times to start from 0
        start_time = times_proc[0] if times_proc else 0
        times_proc = [t - start_time for t in times_proc]

        ax1.plot(times_proc, values_proc, label='Process CPU (%)', color='#3498db', linewidth=2, alpha=0.8)
        ax1.set_xlabel('Time (seconds)', fontsize=12, fontweight='bold')
        ax1.set_ylabel('CPU Usage (%)', fontsize=12, fontweight='bold')
        ax1.set_title('Process CPU Usage Over Time', fontsize=14, fontweight='bold')
        ax1.legend()
        ax1.grid(alpha=0.3)

        # Check if all values are 0
        if all(v == 0 for v in values_proc):
            ax1.text(0.5, 0.95, 'Note: Process CPU is 0% - agents are lightweight\n(most time spent waiting for LLM I/O)',
                    transform=ax1.transAxes, ha='center', va='top', fontsize=9,
                    bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.3))
    else:
        ax1.text(0.5, 0.5, 'No process CPU data available', ha='center', va='center', fontsize=12)
        ax1.set_title('Process CPU Usage Over Time', fontsize=14, fontweight='bold')

    # Chart 2: Process memory usage over time
    # Check if we have per-agent data (distributed system) or combined data (monolithic)
    has_per_agent_mem = 'per_agent' in usage.get('memory', {}) and usage['memory'].get('per_agent')

    if has_per_agent_mem and usage['memory']['per_agent']:
        # Distributed system: plot per-agent memory usage
        colors = plt.cm.Set3(range(len(usage['memory']['per_agent'])))
        for idx, (agent_name, agent_data) in enumerate(usage['memory']['per_agent'].items()):
            if agent_data:
                times, values = zip(*agent_data)
                # Normalize times to start from 0
                start_time = times[0] if times else 0
                times = [t - start_time for t in times]
                ax2.plot(times, values, label=f'{agent_name} Memory (MB)', color=colors[idx], linewidth=2, alpha=0.8)

        ax2.set_xlabel('Time (seconds)', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Memory Usage (MB)', fontsize=12, fontweight='bold')
        ax2.set_title('Process Memory Usage Over Time (Per-Agent)', fontsize=14, fontweight='bold')
        ax2.legend(fontsize=9, loc='best')
        ax2.grid(alpha=0.3)
    elif usage['memory']['process']:
        # Monolithic system: plot combined memory usage
        times_proc, values_proc = zip(*usage['memory']['process'])

        # Normalize times to start from 0
        start_time = times_proc[0] if times_proc else 0
        times_proc = [t - start_time for t in times_proc]

        ax2.plot(times_proc, values_proc, label='Process Memory (MB)', color='#3498db', linewidth=2, alpha=0.8)
        ax2.set_xlabel('Time (seconds)', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Memory Usage (MB)', fontsize=12, fontweight='bold')
        ax2.set_title('Process Memory Usage Over Time', fontsize=14, fontweight='bold')
        ax2.legend()
        ax2.grid(alpha=0.3)
    else:
        ax2.text(0.5, 0.5, 'No process memory data available', ha='center', va='center', fontsize=12)
        ax2.set_title('Process Memory Usage Over Time', fontsize=14, fontweight='bold')

    # Chart 3: Process CPU usage statistics
    ax3.axis('off')
    cpu_stats = []
    if usage['cpu']['process']:
        proc_values = [v for _, v in usage['cpu']['process']]
        if proc_values:
            cpu_stats.append(['Process CPU Mean', f'{np.mean(proc_values):.2f}%'])
            cpu_stats.append(['Process CPU Max', f'{np.max(proc_values):.2f}%'])
            cpu_stats.append(['Process CPU Min', f'{np.min(proc_values):.2f}%'])
            cpu_stats.append(['Process CPU Std', f'{np.std(proc_values):.2f}%'])
            cpu_stats.append(['Non-zero Count', f'{sum(1 for v in proc_values if v > 0)}/{len(proc_values)}'])

    if cpu_stats:
        table = ax3.table(cellText=cpu_stats, colLabels=['Metric', 'Value'],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 2)
        ax3.set_title('Process CPU Usage Statistics', fontsize=14, fontweight='bold', pad=20)
    else:
        ax3.text(0.5, 0.5, 'No process CPU statistics available', ha='center', va='center', fontsize=12)
        ax3.set_title('Process CPU Usage Statistics', fontsize=14, fontweight='bold', pad=20)

    # Chart 4: Process memory usage statistics
    ax4.axis('off')
    mem_stats = []
    if usage['memory']['process']:
        proc_values = [v for _, v in usage['memory']['process']]
        if proc_values:
            mem_stats.append(['Process Memory Mean', f'{np.mean(proc_values):.2f} MB'])
            mem_stats.append(['Process Memory Max', f'{np.max(proc_values):.2f} MB'])
            mem_stats.append(['Process Memory Min', f'{np.min(proc_values):.2f} MB'])
            mem_stats.append(['Process Memory Std', f'{np.std(proc_values):.2f} MB'])

    if mem_stats:
        table = ax4.table(cellText=mem_stats, colLabels=['Metric', 'Value'],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 2)
        ax4.set_title('Process Memory Usage Statistics', fontsize=14, fontweight='bold', pad=20)
    else:
        ax4.text(0.5, 0.5, 'No process memory statistics available', ha='center', va='center', fontsize=12)
        ax4.set_title('Process Memory Usage Statistics', fontsize=14, fontweight='bold', pad=20)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ CPU/Memory usage chart saved to {output_file}")
    plt.close()



def plot_message_sizes(sizes: Dict[str, List[float]], per_agent_sizes: Dict[str, Dict[str, List[float]]],
                      inter_agent_sizes: Dict[Tuple[str, str], Dict[str, List[float]]],
                      agent_llm_sizes: Dict[str, Dict[str, List[float]]], output_file: str):
    """Plot message size charts with per-agent details."""
    fig = plt.figure(figsize=(20, 14))

    # Chart 1: Inter-agent message sizes
    ax1 = plt.subplot(3, 3, 1)
    if sizes['inter_agent_input'] or sizes['inter_agent_output']:
        categories = ['Input', 'Output']
        input_avg = np.mean(sizes['inter_agent_input']) if sizes['inter_agent_input'] else 0
        output_avg = np.mean(sizes['inter_agent_output']) if sizes['inter_agent_output'] else 0
        values = [input_avg, output_avg]

        colors = ['#3498db', '#e74c3c']
        ax1.bar(categories, values, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
        ax1.set_ylabel('Average Message Size (KB)', fontsize=12, fontweight='bold')
        ax1.set_title('Inter-Agent Message Sizes', fontsize=14, fontweight='bold')
        ax1.grid(axis='y', alpha=0.3)

        # Add value labels
        for i, v in enumerate(values):
            if v > 0:
                ax1.text(i, v + max(values) * 0.01 if max(values) > 0 else 0.1, f'{v:.2f} KB',
                        ha='center', va='bottom', fontweight='bold')
    else:
        ax1.axis('off')
        ax1.text(0.5, 0.5, 'No inter-agent message size data\n(In-process calls have no message size)',
                ha='center', va='center', fontsize=10, style='italic')
        ax1.set_title('Inter-Agent Message Sizes', fontsize=14, fontweight='bold')

    # Chart 2: Agent-LLM message sizes (per agent)
    ax2 = plt.subplot(3, 3, 2)
    if agent_llm_sizes:
        agents = []
        input_avgs = []
        output_avgs = []
        for agent in sorted(agent_llm_sizes.keys()):
            agent_data = agent_llm_sizes[agent]
            if agent_data['input'] or agent_data['output']:
                agents.append(agent)
                input_avgs.append(np.mean(agent_data['input']) if agent_data['input'] else 0)
                output_avgs.append(np.mean(agent_data['output']) if agent_data['output'] else 0)

        if agents:
            x = np.arange(len(agents))
            width = 0.35
            ax2.bar(x - width/2, input_avgs, width, label='Input', color='#3498db', alpha=0.8, edgecolor='black')
            ax2.bar(x + width/2, output_avgs, width, label='Output', color='#e74c3c', alpha=0.8, edgecolor='black')
            ax2.set_xlabel('Agent', fontsize=10, fontweight='bold')
            ax2.set_ylabel('Avg Message Size (KB)', fontsize=10, fontweight='bold')
            ax2.set_title('Agent-LLM Message Sizes (per Agent)', fontsize=12, fontweight='bold')
            ax2.set_xticks(x)
            ax2.set_xticklabels(agents, rotation=45, ha='right', fontsize=8)
            ax2.legend()
            ax2.grid(axis='y', alpha=0.3)
    elif sizes['agent_llm_input'] or sizes['agent_llm_output']:
        # Fallback to overall if no per-agent data
        categories = ['Input', 'Output']
        input_avg = np.mean(sizes['agent_llm_input']) if sizes['agent_llm_input'] else 0
        output_avg = np.mean(sizes['agent_llm_output']) if sizes['agent_llm_output'] else 0
        values = [input_avg, output_avg]

        colors = ['#3498db', '#e74c3c']
        ax2.bar(categories, values, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
        ax2.set_ylabel('Average Message Size (KB)', fontsize=12, fontweight='bold')
        ax2.set_title('Agent-LLM Message Sizes', fontsize=14, fontweight='bold')
        ax2.grid(axis='y', alpha=0.3)

        # Add value labels
        for i, v in enumerate(values):
            if v > 0:
                ax2.text(i, v + max(values) * 0.01 if max(values) > 0 else 0.1, f'{v:.2f} KB',
                        ha='center', va='bottom', fontweight='bold')

    # Chart 3: Per-agent message sizes (input)
    ax3 = plt.subplot(3, 3, 3)
    if per_agent_sizes:
        agents = []
        input_avgs = []
        output_avgs = []
        for agent in sorted(per_agent_sizes.keys()):
            agent_data = per_agent_sizes[agent]
            if agent_data['input'] or agent_data['output']:
                agents.append(agent)
                input_avgs.append(np.mean(agent_data['input']) if agent_data['input'] else 0)
                output_avgs.append(np.mean(agent_data['output']) if agent_data['output'] else 0)

        if agents:
            x = np.arange(len(agents))
            width = 0.35
            ax3.bar(x - width/2, input_avgs, width, label='Input', color='#3498db', alpha=0.8, edgecolor='black')
            ax3.bar(x + width/2, output_avgs, width, label='Output', color='#e74c3c', alpha=0.8, edgecolor='black')
            ax3.set_xlabel('Agent', fontsize=10, fontweight='bold')
            ax3.set_ylabel('Avg Message Size (KB)', fontsize=10, fontweight='bold')
            ax3.set_title('Message Size per Agent', fontsize=12, fontweight='bold')
            ax3.set_xticks(x)
            ax3.set_xticklabels(agents, rotation=45, ha='right', fontsize=8)
            ax3.legend()
            ax3.grid(axis='y', alpha=0.3)
        else:
            ax3.axis('off')
            ax3.text(0.5, 0.5, 'No per-agent message size data', ha='center', va='center', fontsize=10, style='italic')
            ax3.set_title('Message Size per Agent', fontsize=12, fontweight='bold')
    else:
        ax3.axis('off')
        ax3.text(0.5, 0.5, 'No per-agent message size data', ha='center', va='center', fontsize=10, style='italic')
        ax3.set_title('Message Size per Agent', fontsize=12, fontweight='bold')

    # Chart 4: Inter-agent message sizes detail
    ax4 = plt.subplot(3, 3, 4)
    if inter_agent_sizes:
        pairs = []
        input_avgs = []
        output_avgs = []
        for (src, tgt) in sorted(inter_agent_sizes.keys()):
            pair_data = inter_agent_sizes[(src, tgt)]
            if pair_data['input'] or pair_data['output']:
                pairs.append(f'{src}→{tgt}')
                input_avgs.append(np.mean(pair_data['input']) if pair_data['input'] else 0)
                output_avgs.append(np.mean(pair_data['output']) if pair_data['output'] else 0)

        if pairs:
            x = np.arange(len(pairs))
            width = 0.35
            ax4.bar(x - width/2, input_avgs, width, label='Input', color='#3498db', alpha=0.8, edgecolor='black')
            ax4.bar(x + width/2, output_avgs, width, label='Output', color='#e74c3c', alpha=0.8, edgecolor='black')
            ax4.set_xlabel('Communication Pair', fontsize=10, fontweight='bold')
            ax4.set_ylabel('Avg Message Size (KB)', fontsize=10, fontweight='bold')
            ax4.set_title('Message Size by Communication Pair', fontsize=12, fontweight='bold')
            ax4.set_xticks(x)
            ax4.set_xticklabels(pairs, rotation=45, ha='right', fontsize=7)
            ax4.legend()
            ax4.grid(axis='y', alpha=0.3)
        else:
            ax4.axis('off')
            ax4.text(0.5, 0.5, 'No inter-agent pair data\n(In-process calls have no message size)',
                    ha='center', va='center', fontsize=10, style='italic')
            ax4.set_title('Message Size by Communication Pair', fontsize=12, fontweight='bold')
    else:
        ax4.axis('off')
        ax4.text(0.5, 0.5, 'No inter-agent pair data\n(In-process calls have no message size)',
                ha='center', va='center', fontsize=10, style='italic')
        ax4.set_title('Message Size by Communication Pair', fontsize=12, fontweight='bold')

    # Chart 5: Message size distribution (inter-agent)
    ax5 = plt.subplot(3, 3, 5)
    if sizes['inter_agent_input'] or sizes['inter_agent_output']:
        all_sizes = sizes['inter_agent_input'] + sizes['inter_agent_output']
        if all_sizes:
            ax5.hist(all_sizes, bins=20, color='#3498db', alpha=0.7, edgecolor='black')
            ax5.axvline(np.mean(all_sizes), color='red', linestyle='--', linewidth=2,
                       label=f'Mean: {np.mean(all_sizes):.2f} KB')
            ax5.set_xlabel('Message Size (KB)', fontsize=10, fontweight='bold')
            ax5.set_ylabel('Frequency', fontsize=10, fontweight='bold')
            ax5.set_title('Inter-Agent Message Size Distribution', fontsize=12, fontweight='bold')
            ax5.legend()
            ax5.grid(axis='y', alpha=0.3)
    else:
        ax5.axis('off')
        ax5.text(0.5, 0.5, 'No inter-agent message size data', ha='center', va='center', fontsize=10)

    # Chart 6: Message size statistics
    ax6 = plt.subplot(3, 3, 6)
    ax6.axis('off')
    stats_data = [['Metric', 'Value']]

    if sizes['inter_agent_input']:
        stats_data.append(['Inter-Agent Input Mean', f'{np.mean(sizes["inter_agent_input"]):.2f} KB'])
        stats_data.append(['Inter-Agent Input Total', f'{np.sum(sizes["inter_agent_input"]):.2f} KB'])
    if sizes['inter_agent_output']:
        stats_data.append(['Inter-Agent Output Mean', f'{np.mean(sizes["inter_agent_output"]):.2f} KB'])
        stats_data.append(['Inter-Agent Output Total', f'{np.sum(sizes["inter_agent_output"]):.2f} KB'])
    if sizes['agent_llm_input']:
        stats_data.append(['Agent-LLM Input Mean', f'{np.mean(sizes["agent_llm_input"]):.2f} KB'])
        stats_data.append(['Agent-LLM Input Total', f'{np.sum(sizes["agent_llm_input"]):.2f} KB'])
    if sizes['agent_llm_output']:
        stats_data.append(['Agent-LLM Output Mean', f'{np.mean(sizes["agent_llm_output"]):.2f} KB'])
        stats_data.append(['Agent-LLM Output Total', f'{np.sum(sizes["agent_llm_output"]):.2f} KB'])

    if len(stats_data) > 1:
        table = ax6.table(cellText=stats_data[1:], colLabels=stats_data[0],
                         cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.5)
        ax6.set_title('Message Size Statistics', fontsize=12, fontweight='bold', pad=10)

    # Chart 7-9: Additional details
    ax7 = plt.subplot(3, 3, 7)
    ax7.axis('off')
    if per_agent_sizes:
        agent_stats = []
        for agent in sorted(per_agent_sizes.keys()):
            agent_data = per_agent_sizes[agent]
            input_total = np.sum(agent_data['input']) if agent_data['input'] else 0
            output_total = np.sum(agent_data['output']) if agent_data['output'] else 0
            if input_total > 0 or output_total > 0:
                agent_stats.append([agent, f'{input_total:.2f}', f'{output_total:.2f}'])

        if agent_stats:
            table = ax7.table(cellText=agent_stats, colLabels=['Agent', 'Input Total (KB)', 'Output Total (KB)'],
                            cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
            table.auto_set_font_size(False)
            table.set_fontsize(8)
            table.scale(1, 1.5)
            ax7.set_title('Per-Agent Message Totals', fontsize=12, fontweight='bold', pad=10)

    ax8 = plt.subplot(3, 3, 8)
    ax8.axis('off')
    if inter_agent_sizes:
        comm_stats = []
        for (src, tgt) in sorted(inter_agent_sizes.keys()):
            pair_data = inter_agent_sizes[(src, tgt)]
            input_total = np.sum(pair_data['input']) if pair_data['input'] else 0
            output_total = np.sum(pair_data['output']) if pair_data['output'] else 0
            if input_total > 0 or output_total > 0:
                comm_stats.append([f'{src}→{tgt}', f'{input_total:.2f}', f'{output_total:.2f}'])

        if comm_stats:
            table = ax8.table(cellText=comm_stats[:10], colLabels=['Pair', 'Input Total (KB)', 'Output Total (KB)'],
                            cellLoc='center', loc='center', bbox=[0, 0, 1, 1])
            table.auto_set_font_size(False)
            table.set_fontsize(7)
            table.scale(1, 1.2)
            ax8.set_title('Inter-Agent Totals (Top 10)', fontsize=12, fontweight='bold', pad=10)

    ax9 = plt.subplot(3, 3, 9)
    ax9.axis('off')
    note_text = "Note: Message sizes are calculated from actual data:\n"
    note_text += "- Inter-agent: tool_call_args and tool_response\n"
    note_text += "- Agent-LLM: llm_request and llm_response\n"
    note_text += "All sizes are UTF-8 byte lengths (KB)"
    ax9.text(0.1, 0.5, note_text, fontsize=9, verticalalignment='center',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    ax9.set_title('Measurement Notes', fontsize=12, fontweight='bold', pad=10)

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Message size chart saved to {output_file}")
    plt.close()


def plot_events_with_cpu_memory(traces: List[Dict], cpu_memory_usage: Dict, output_file: str):
    """Plot spans/events timeline (Gantt chart) with CPU/memory usage below.

    Creates a two-panel figure:
    - Top: Gantt chart showing spans (or events if available) and their durations
    - Bottom: CPU and memory usage over time
    - Both panels share the same time axis with vertical lines connecting spans/events to resource usage

    Args:
        traces: List of trace spans (used to extract events or spans for Gantt chart)
        cpu_memory_usage: Dict from extract_cpu_memory_usage()
        output_file: Path to save the figure
    """

    # Extract events from spans
    events = []
    for span in traces:
        span_events = span.get('events', [])
        if span_events:
            span_name = span.get('name', 'unknown')
            span_start = span.get('start_time', 0)
            span_end = span.get('end_time', 0)

            # Handle both integer (nanoseconds) and ISO string timestamps
            if isinstance(span_start, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_start.replace('Z', '+00:00'))
                    span_start = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_start = 0
            if isinstance(span_end, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_end.replace('Z', '+00:00'))
                    span_end = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_end = 0

            attrs = span.get('attributes', {})
            agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')

            for event in span_events:
                event_timestamp = event.get('timestamp', 0)
                # Handle both integer (nanoseconds) and ISO string timestamps
                if isinstance(event_timestamp, str):
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                        event_timestamp = int(dt.timestamp() * 1e9)
                    except (ValueError, AttributeError):
                        event_timestamp = 0

                events.append({
                    'name': event.get('name', 'unknown'),
                    'timestamp': event_timestamp,
                    'span_name': span_name,
                    'span_start': span_start,
                    'span_end': span_end,
                    'agent_name': agent_name,
                    'attributes': event.get('attributes', {})
                })

    # If no events or very few events, use spans as Gantt chart items
    # Use spans if we have less than 5 events (spans are more informative for visualization)
    use_spans_as_events = len(events) < 5
    if use_spans_as_events:
        print(f"   Using spans mode: {len(events)} events found, using {len(traces)} spans instead")
    else:
        print(f"   Using events mode: {len(events)} events found")

    if use_spans_as_events:
        # Create span-based timeline
        spans_for_gantt = []
        for span in traces:
            span_start = span.get('start_time', 0)
            span_end = span.get('end_time', 0)

            # Handle both integer (nanoseconds) and ISO string timestamps
            if isinstance(span_start, str):
                # ISO format string, convert to nanoseconds
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_start.replace('Z', '+00:00'))
                    span_start = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_start = 0
            if isinstance(span_end, str):
                # ISO format string, convert to nanoseconds
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_end.replace('Z', '+00:00'))
                    span_end = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_end = 0

            if span_start > 0 and span_end > 0:
                attrs = span.get('attributes', {})
                agent_name = attrs.get('gen_ai.agent.name') or span.get('agent_name', 'unknown')
                spans_for_gantt.append({
                    'name': span.get('name', 'unknown'),
                    'start': span_start,
                    'end': span_end,
                    'agent_name': agent_name,
                    'span_id': span.get('span_id', '')
                })

        if not spans_for_gantt:
            print("⚠️  No spans or events data to plot")
            return
        else:
            print(f"   Using {len(spans_for_gantt)} spans for Gantt chart (no events found in data)")
            # Debug: Check first span timing
            if spans_for_gantt:
                first_span = spans_for_gantt[0]
                print(f"   First span: {first_span['name']}, start={first_span['start']}, end={first_span['end']}")

    # Check if we have events data (for events mode)
    # Even without CPU/memory data, we should still plot events/spans timeline
    if not use_spans_as_events:
        if not events:
            print("⚠️  No events data to plot")
            return
        # Warn if no CPU/memory data, but still proceed to plot events timeline
        if not cpu_memory_usage.get('cpu', {}).get('process'):
            print("   ⚠️  Warning: No CPU/memory data available, will plot events timeline only")

    fig = plt.figure(figsize=(20, 12))

    # Determine time range and normalize to relative time (start from 0)
    cpu_data = cpu_memory_usage.get('cpu', {}).get('process', [])
    mem_data = cpu_memory_usage.get('memory', {}).get('process', [])

    # Get base time from the earliest data
    base_time = None
    all_times = []

    if use_spans_as_events:
        span_start_times = [s['start'] / 1e9 for s in spans_for_gantt]
        span_end_times = [s['end'] / 1e9 for s in spans_for_gantt]
        all_times.extend(span_start_times)
        all_times.extend(span_end_times)
    else:
        event_times = [e['timestamp'] / 1e9 for e in events]
        # For events mode, also include all trace start/end times to ensure base_time
        # is calculated from the earliest trace, not just from events' span times
        # (events' spans may start much later in the run)
        trace_start_times = [span.get('start_time', 0) / 1e9 for span in traces if span.get('start_time', 0) > 0]
        trace_end_times = [span.get('end_time', 0) / 1e9 for span in traces if span.get('end_time', 0) > 0]
        span_starts = [e['span_start'] / 1e9 for e in events if e['span_start'] > 0]
        span_ends = [e['span_end'] / 1e9 for e in events if e['span_end'] > 0]
        all_times.extend(event_times + trace_start_times + trace_end_times + span_starts + span_ends)

    if cpu_data:
        cpu_times = [t for t, _ in cpu_data]
        all_times.extend(cpu_times)
    if mem_data:
        mem_times = [t for t, _ in mem_data]
        all_times.extend(mem_times)

    if not all_times:
        print("⚠️  No time data available")
        return

    base_time = min(all_times)
    max_time = max(all_times) - base_time

    # Debug: Print time ranges for troubleshooting
    if traces:
        first_trace_start = traces[0].get('start_time', 0)
        if isinstance(first_trace_start, (int, float)):
            first_trace_start_sec = first_trace_start / 1e9 if first_trace_start > 1e12 else first_trace_start
            print(f"   First trace start_time (raw): {first_trace_start_sec:.2f}, name: {traces[0].get('name', 'unknown')[:30]}")
    if cpu_data:
        print(f"   First CPU time (raw): {cpu_data[0][0]:.2f}")
    if use_spans_as_events:
        if spans_for_gantt:
            span_times = [(s['start'] / 1e9) - base_time for s in spans_for_gantt[:3]]
            print(f"   First 3 span times (normalized): {[f'{t:.2f}' for t in span_times]}")
    else:
        if events:
            event_times_norm = [(e['timestamp'] / 1e9) - base_time for e in events[:3]]
            print(f"   First 3 event times (normalized): {[f'{t:.2f}' for t in event_times_norm]}")
    if cpu_data:
        cpu_times_norm = [(t - base_time) for t, _ in cpu_data[:3]]
        print(f"   First 3 CPU times (normalized): {[f'{t:.2f}' for t in cpu_times_norm]}")
    print(f"   Base time: {base_time:.2f}, Max time: {max_time:.2f}")



    # Create two subplots with shared x-axis
    # Use sharex to ensure both panels have synchronized x-axis
    gs = fig.add_gridspec(2, 1, height_ratios=[1, 1], hspace=0.15)
    ax1 = fig.add_subplot(gs[0])  # Top: Spans/Events Gantt chart
    ax2 = fig.add_subplot(gs[1], sharex=ax1)  # Bottom: CPU/Memory (shares x-axis with ax1)

    # ========== Top Panel: Spans/Events Gantt Chart ==========
    if use_spans_as_events:
        # Group spans by agent or operation type for better visualization
        span_groups = {}
        for span_info in spans_for_gantt:
            # Use agent name if available, otherwise use span name prefix
            group_key = span_info['agent_name'] if span_info['agent_name'] != 'unknown' else span_info['name'].split()[0] if span_info['name'] else 'unknown'
            if group_key not in span_groups:
                span_groups[group_key] = []
            span_groups[group_key].append(span_info)

        # Sort groups by first span start time
        sorted_groups = sorted(span_groups.items(), key=lambda x: min(s['start'] / 1e9 for s in x[1]))

        # Create Gantt chart
        y_positions = {}
        y_pos = 0
        colors = plt.cm.Set3(np.linspace(0, 1, len(sorted_groups)))

        drawn_spans = 0
        skipped_spans = 0

        for i, (group_name, group_spans) in enumerate(sorted_groups):
            # Sort spans in group by start time
            group_spans.sort(key=lambda x: x['start'])

            for span_info in group_spans:
                if group_name not in y_positions:
                    y_positions[group_name] = y_pos
                    y_pos += 1

                y = y_positions[group_name]
                start = (span_info['start'] / 1e9) - base_time
                end = (span_info['end'] / 1e9) - base_time
                duration = end - start

                if duration > 0:
                    # Draw span bar
                    ax1.barh(y, duration, left=start, height=0.6, color=colors[i],
                            alpha=0.7, edgecolor='black', linewidth=1)
                    drawn_spans += 1

                    # Add span name label (truncate if too long)
                    span_name = span_info['name']
                    if len(span_name) > 50:
                        span_name = span_name[:47] + '...'
                    mid_time = start + duration / 2
                    ax1.text(mid_time, y, span_name,
                            ha='center', va='center', fontsize=7, fontweight='bold',
                            color='black', bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.7))
                else:
                    skipped_spans += 1

        if drawn_spans == 0:
            print(f"   ⚠️  Warning: No spans were drawn! (skipped {skipped_spans} spans with duration <= 0)")
        else:
            print(f"   ✓ Drew {drawn_spans} spans in {len(sorted_groups)} groups (skipped {skipped_spans} spans)")

        ax1.set_ylabel('Span/Agent', fontsize=12, fontweight='bold')
        ax1.set_title(f'Spans Timeline (Gantt Chart) - {len(spans_for_gantt)} spans', fontsize=14, fontweight='bold')
        ax1.set_yticks(list(range(len(y_positions))))
        ax1.set_yticklabels([name[:40] + '...' if len(name) > 40 else name
                            for name in y_positions.keys()], fontsize=8)
        ax1.grid(axis='x', alpha=0.3)
        # Don't set xlim here - set it after both panels are drawn to ensure synchronization
        ax1.invert_yaxis()  # Top to bottom order
    else:
        # Use events (original logic)
        span_data = {}
        for event in events:
            span_name = event['span_name']
            # Handle both integer (nanoseconds) and ISO string timestamps
            span_start = event['span_start']
            span_end = event['span_end']
            event_timestamp = event['timestamp']

            if isinstance(span_start, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_start.replace('Z', '+00:00'))
                    span_start = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_start = 0
            if isinstance(span_end, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(span_end.replace('Z', '+00:00'))
                    span_end = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    span_end = 0
            if isinstance(event_timestamp, str):
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                    event_timestamp = int(dt.timestamp() * 1e9)
                except (ValueError, AttributeError):
                    event_timestamp = 0

            if span_name not in span_data:
                span_data[span_name] = {
                    'start': (span_start / 1e9) - base_time,
                    'end': (span_end / 1e9) - base_time,
                    'events': [],
                    'agent_name': event['agent_name']
                }
            span_data[span_name]['events'].append({
                'name': event['name'],
                'timestamp': (event_timestamp / 1e9) - base_time,
                'attributes': event['attributes']
            })

        sorted_spans = sorted(span_data.items(), key=lambda x: x[1]['start'])
        y_positions = {}
        y_pos = 0
        colors = plt.cm.Set3(np.linspace(0, 1, len(sorted_spans)))

        drawn_spans = 0
        drawn_events = 0

        for i, (span_name, span_info) in enumerate(sorted_spans):
            if span_name not in y_positions:
                y_positions[span_name] = y_pos
                y_pos += 1

            y = y_positions[span_name]
            start = span_info['start']
            end = span_info['end']
            duration = end - start

            if duration > 0:
                ax1.barh(y, duration, left=start, height=0.6, color=colors[i],
                        alpha=0.7, edgecolor='black', linewidth=1)
                drawn_spans += 1

            for event in span_info['events']:
                event_time = event['timestamp']
                ax1.axvline(x=event_time, color='gray', linestyle='--',
                          linewidth=1.5, alpha=0.6, zorder=1)
                ax1.plot(event_time, y, 'ro', markersize=8, alpha=0.9, zorder=2)
                drawn_events += 1

        print(f"   ✓ Drew {drawn_spans} span bars and {drawn_events} event markers")
        if drawn_spans == 0 and drawn_events == 0:
            print(f"   ⚠️  Warning: No spans or events were drawn! Check time ranges.")
            if span_data:
                first_span = list(span_data.values())[0]
                print(f"   First span start: {first_span['start']}, end: {first_span['end']}, base_time: {base_time}")

        ax1.set_ylabel('Span/Event', fontsize=12, fontweight='bold')
        ax1.set_title(f'Events Timeline (Gantt Chart) - {len(events)} events', fontsize=14, fontweight='bold')
        ax1.set_yticks(list(range(len(y_positions))))
        ax1.set_yticklabels([name[:40] + '...' if len(name) > 40 else name
                            for name in y_positions.keys()], fontsize=8)
        ax1.grid(axis='x', alpha=0.3)
        # Don't set xlim here - set it after both panels are drawn to ensure synchronization
        ax1.invert_yaxis()

    # ========== Bottom Panel: CPU/Memory Usage ==========
    # Initialize variables for xlim calculation
    cpu_times_list = []
    mem_times_list = []

    # Check if we have CPU/memory data
    has_cpu_memory_data = cpu_data or mem_data

    if has_cpu_memory_data:
        # Normalize times
        cpu_times_norm = [(t - base_time, v) for t, v in cpu_data]
        mem_times_norm = [(t - base_time, v) for t, v in mem_data]

        # Plot CPU usage
        if cpu_times_norm:
            cpu_times, cpu_values = zip(*cpu_times_norm)
            cpu_times_list = list(cpu_times)  # Store for xlim calculation
            ax2.plot(cpu_times, cpu_values, 'b-', linewidth=2, label='CPU Usage (%)', alpha=0.8)

        # Plot Memory usage (on secondary y-axis)
        ax2_mem = ax2.twinx()
        if mem_times_norm:
            mem_times, mem_values = zip(*mem_times_norm)
            mem_times_list = list(mem_times)  # Store for xlim calculation
            ax2_mem.plot(mem_times, mem_values, 'r-', linewidth=2, label='Memory Usage (MB)', alpha=0.8)

        # Draw vertical lines from spans/events to resource usage
        # These lines connect the top panel (spans/events) to the bottom panel (CPU/memory)
        if use_spans_as_events:
            for span_info in spans_for_gantt:
                start = (span_info['start'] / 1e9) - base_time
                end = (span_info['end'] / 1e9) - base_time
                # Draw lines at span start and end to connect to CPU/memory plot
                ax2.axvline(x=start, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                ax2.axvline(x=end, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                # Also draw on top panel for visual connection
                ax1.axvline(x=start, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                ax1.axvline(x=end, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
        elif events:
            for event in events:
                event_time = (event['timestamp'] / 1e9) - base_time
                ax2.axvline(x=event_time, color='gray', linestyle='--',
                          linewidth=1.5, alpha=0.6, zorder=0)
                ax1.axvline(x=event_time, color='gray', linestyle='--',
                          linewidth=1.5, alpha=0.6, zorder=0)

        ax2.set_xlabel('Time (seconds, relative)', fontsize=12, fontweight='bold')
        ax2.set_ylabel('CPU Usage (%)', fontsize=12, fontweight='bold', color='blue')
        ax2.tick_params(axis='y', labelcolor='blue')
        if mem_times_norm:
            ax2_mem.set_ylabel('Memory Usage (MB)', fontsize=12, fontweight='bold', color='red')
            ax2_mem.tick_params(axis='y', labelcolor='red')
        ax2.set_title('CPU and Memory Usage Over Time', fontsize=14, fontweight='bold')
        ax2.grid(axis='x', alpha=0.3)
    else:
        # No CPU/memory data, but still show the panel with a message
        ax2.text(0.5, 0.5, 'No CPU/Memory data available',
                ha='center', va='center', fontsize=14,
                transform=ax2.transAxes, style='italic', color='gray')
        ax2.set_xlabel('Time (seconds, relative)', fontsize=12, fontweight='bold')
        ax2.set_title('CPU and Memory Usage Over Time (No Data)', fontsize=14, fontweight='bold')
        ax2.grid(axis='x', alpha=0.3)

        # Still draw vertical lines from spans/events even without CPU/memory data
        # This helps visualize the timeline alignment
        if use_spans_as_events:
            for span_info in spans_for_gantt:
                start = (span_info['start'] / 1e9) - base_time
                end = (span_info['end'] / 1e9) - base_time
                ax2.axvline(x=start, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                ax2.axvline(x=end, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                ax1.axvline(x=start, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
                ax1.axvline(x=end, color='gray', linestyle='--',
                          linewidth=1, alpha=0.4, zorder=0)
        elif events:
            for event in events:
                event_time = (event['timestamp'] / 1e9) - base_time
                ax2.axvline(x=event_time, color='gray', linestyle='--',
                          linewidth=1.5, alpha=0.6, zorder=0)
                ax1.axvline(x=event_time, color='gray', linestyle='--',
                          linewidth=1.5, alpha=0.6, zorder=0)

        # Set x-axis limits for both panels AFTER all drawing is complete
        # This ensures perfect synchronization when using sharex
        # Calculate the actual max time from all drawn elements
        all_drawn_times = []
        if use_spans_as_events:
            for span_info in spans_for_gantt:
                start = (span_info['start'] / 1e9) - base_time
                end = (span_info['end'] / 1e9) - base_time
                all_drawn_times.extend([start, end])
        else:
            # Include events and spans times
            for span_name, span_info in span_data.items():
                all_drawn_times.extend([span_info['start'], span_info['end']])
                for event in span_info['events']:
                    all_drawn_times.append(event['timestamp'])
        if has_cpu_memory_data:
            all_drawn_times.extend(cpu_times_list)
            all_drawn_times.extend(mem_times_list)

        if all_drawn_times:
            actual_max_time = max(all_drawn_times)
            # Add small padding (1%)
            actual_max_time = actual_max_time * 1.01
        else:
            actual_max_time = max_time

        # Set limits on both axes to ensure perfect alignment
        # This must be done after all drawing to ensure sharex works correctly
        ax1.set_xlim(0, actual_max_time)
        ax2.set_xlim(0, actual_max_time)

        # Combine legends (only if we have memory data)
        if has_cpu_memory_data and mem_times_norm:
            lines1, labels1 = ax2.get_legend_handles_labels()
            lines2, labels2 = ax2_mem.get_legend_handles_labels()
            ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=10)
        elif has_cpu_memory_data:
            ax2.legend(loc='upper left', fontsize=10)
        ax2.set_xlim(0, max_time)

    plt.subplots_adjust(hspace=0.15)
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Events/spans with CPU/memory chart saved to {output_file}")
    plt.close()


def plot_latency_flame(traces: List[Dict], output_file: str, title: str = "Request Latency Flame Graph"):
    """Render a span-based aggregated (latency) flame graph.

    This is a request/latency-style flame graph (not CPU). Width = aggregated span duration
    across all traces; depth = parent/child span nesting. Good for seeing where time goes.
    """
    spans = [s for s in traces if s.get('duration_ns', 0) > 0]
    if not spans:
        print(f"⚠️ No spans with duration found; skip latency flame graph for {output_file}")
        return

    span_by_id = {s.get('span_id'): s for s in spans if s.get('span_id')}
    collapsed_stacks = defaultdict(float)

    def frame_label(span: Dict) -> str:
        attrs = span.get('attributes', {}) or {}
        res_attrs = (span.get('resource', {}) or {}).get('attributes', {}) or {}
        service = res_attrs.get('service.name') or attrs.get('service.name') or attrs.get('gen_ai.agent.name') or attrs.get('adk.agent.name')
        op = span.get('name', 'span')
        parts = [p for p in [service, op] if p]
        label = ":".join(parts) if parts else op
        label = label.replace('_agent', '').replace(' agent', '').replace('_', '-').strip()
        return label or 'span'

    # Build collapsed stacks: "root;child;leaf" -> aggregated duration (ms)
    for span in spans:
        duration_ms = span.get('duration_ns', 0) / 1e6
        if duration_ms <= 0:
            continue
        path = []
        current = span
        visited = set()
        while current:
            sid = current.get('span_id')
            if sid in visited:
                break
            if sid:
                visited.add(sid)
            path.append(frame_label(current))
            parent_id = current.get('parent_span_id')
            current = span_by_id.get(parent_id) if parent_id else None
        if not path:
            continue
        stack_key = ";".join(reversed(path))  # root -> leaf
        collapsed_stacks[stack_key] += duration_ms

    if not collapsed_stacks:
        print(f"⚠️ No stack data after aggregation; skip latency flame graph for {output_file}")
        return

    # Build aggregated tree
    root = {"name": "root", "value": 0.0, "children": {}}
    for stack, value in collapsed_stacks.items():
        frames = stack.split(';')
        root["value"] += value
        node = root
        for frame in frames:
            child = node["children"].setdefault(frame, {"name": frame, "value": 0.0, "children": {}})
            child["value"] += value
            node = child

    # Layout: recursive icicle; sort siblings by value desc for readability
    rects = []
    max_depth = 0

    def layout(node, x_start, depth):
        nonlocal max_depth
        children = sorted(node["children"].values(), key=lambda c: c["value"], reverse=True)
        x = x_start
        for child in children:
            rects.append((x, depth, child["value"], child["name"]))
            max_depth = max(max_depth, depth)
            x = layout(child, x, depth + 1)
        return x_start + node["value"]

    layout(root, 0.0, 0)

    if not rects or root["value"] <= 0:
        print(f"⚠️ Nothing to draw for latency flame graph {output_file}")
        return

    total = root["value"]
    fig, ax = plt.subplots(figsize=(20, max(6, max_depth + 3)))
    cmap = plt.cm.tab20

    def color_for(name: str):
        return cmap(hash(name) % cmap.N)

    for x, y, w, name in rects:
        rect = plt.Rectangle((x, y), w, 0.9, facecolor=color_for(name), edgecolor='black', linewidth=0.5, alpha=0.85)
        ax.add_patch(rect)
        if w >= total * 0.015:
            ax.text(x + w / 2, y + 0.45, f"{name}\n{w:.1f} ms", ha='center', va='center', fontsize=8, wrap=True)

    ax.set_xlim(0, total * 1.02)
    ax.set_ylim(-0.5, max_depth + 1.5)
    ax.set_xlabel('Aggregated span duration (ms) across traces')
    ax.set_ylabel('Stack depth (root at bottom)')
    ax.set_title(title, fontsize=14, fontweight='bold')
    ax.grid(False)
    ax.set_xticks(np.linspace(0, total, num=5))

    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Latency flame graph saved to {output_file}")
    plt.close()
