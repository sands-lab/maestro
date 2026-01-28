#!/usr/bin/env python3
"""
Plot metrics for examples from OpenTelemetry trace and metrics data.

This script uses the observability module to generate visualizations.
Can be run from any project directory that contains traces/ and metrics/ directories.

Supports both command-line arguments and configuration files for persistent settings.

New unified mode: Run from plot directory and specify examples by name.
If no arguments are provided, defaults to plot/configs/all_examples_parquet_plot_config.json
and runs both latest and per_run modes.
"""

import sys
import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

# Add observability module to path
# This script can be run from:
# 1. plot/ directory directly (recommended for unified mode)
# 2. Any project subdirectory (marketing-agency, image-scoring, etc.)
# 3. Project root directory
_script_dir = Path(__file__).parent
_project_root = _script_dir.parent  # plot -> project root
DEFAULT_EXAMPLES_CONFIG = _script_dir / "configs" / "all_examples_parquet_plot_config.json"
if __package__ in (None, ""):
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
    __package__ = "plot"

from .lib.analyzer import MetricsAnalyzer


def load_config(config_file: Path) -> dict:
    """Load configuration from JSON file."""
    if not config_file.exists():
        return {}

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except Exception as e:
        print(f"Warning: Failed to load config from {config_file}: {e}")
        return {}


def save_config(config: dict, config_file: Path):
    """Save configuration to JSON file."""
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"Configuration saved to {config_file}")
    except Exception as e:
        print(f"Warning: Failed to save config to {config_file}: {e}")


def parse_examples_config(config_file: Path) -> Dict[str, Dict[str, str]]:
    """Parse examples configuration from JSON file.

    Expected format:
    {
      "example_name": {
        "traces_dir": "path/to/traces",
        "metrics_dir": "path/to/metrics",
        "base_dir": "path/to/base"  # optional
      },
      ...
    }
    """
    if not config_file.exists():
        raise FileNotFoundError(f"Examples config file not found: {config_file}")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except Exception as e:
        raise ValueError(f"Failed to parse examples config from {config_file}: {e}")


def _use_legacy_mode(args: argparse.Namespace) -> bool:
    legacy_arg_names = (
        "traces_dir",
        "metrics_dir",
        "base_dir",
        "output_dir",
        "config",
        "save_config",
    )
    if args.example or args.examples_config:
        return False
    if any(getattr(args, name) for name in legacy_arg_names):
        return True
    cwd = Path.cwd()
    return (cwd / "traces").exists() or (cwd / "metrics").exists()


def plot_tree_of_thoughts_model_similarity() -> None:
    """Plot call graph similarity violins across tree-of-thoughts models."""
    from .lib.comparison import MultiExampleCollector, CrossExampleVisualizer

    tot_root = _project_root / "yxc" / "tree-of-thoughts"
    model_dirs = [
        (
            'gemini-2.0-flash-lite',
            str(tot_root / "spans" / "gemini-2.0-flash-lite"),
            str(tot_root / "metrics" / "gemini-2.0-flash-lite"),
        ),
        (
            'gemini-2.5-flash',
            str(tot_root / "spans" / "gemini-2.5-flash"),
            str(tot_root / "metrics" / "gemini-2.5-flash"),
        ),
        (
            'gemini-2.5-flash-lite',
            str(tot_root / "spans" / "gemini-2.5-flash-lite"),
            str(tot_root / "metrics" / "gemini-2.5-flash-lite"),
        ),
        (
            'gpt-4o-mini',
            str(tot_root / "spans" / "gpt-4o-mini"),
            str(tot_root / "metrics" / "gpt-4o-mini"),
        ),
        (
            'gpt-5o-mini',
            str(tot_root / "spans" / "gpt-5o-mini"),
            str(tot_root / "metrics" / "gpt-5o-mini"),
        ),
        (
            'gpt-5o-nano',
            str(tot_root / "spans" / "gpt-5o-nano"),
            str(tot_root / "metrics" / "gpt-5o-nano"),
        ),
    ]

    collector = MultiExampleCollector()
    for model_name, traces_dir, metrics_dir in model_dirs:
        print(f"Loading model traces: {model_name}...")
        try:
            collector.add_example(
                example_name=model_name,
                traces_dir=traces_dir,
                metrics_dir=metrics_dir,
                analysis_mode='per_run'
            )
            example = collector.examples[model_name]
            num_runs = len(example.call_graphs_per_run) if example.call_graphs_per_run else 0
            print(f"  ✓ Loaded {model_name} ({num_runs} runs)")
        except Exception as e:
            print(f"  ✗ Error loading {model_name}: {e}")
            continue

    if not collector.examples:
        print("Error: No model data loaded for tree-of-thoughts")
        return

    output_dir = _script_dir / 'figures' / 'comparison'
    output_dir.mkdir(parents=True, exist_ok=True)

    visualizer = CrossExampleVisualizer(collector)
    loaded_models = [name for name, _, _ in model_dirs if name in collector.examples]
    output_file = output_dir / 'call_graph_similarity_by_model.pdf'
    visualizer.plot_call_graph_similarity_violins(
        str(output_file),
        examples=loaded_models,
        x_label='Model'
    )
    print(f"Model call graph similarity plot saved to: {output_file}")


def process_example(
    example_name: str,
    example_config: Dict[str, Optional[str]],
    mode: str,
    project_root: Path
) -> None:
    """Process a single example and generate visualizations.

    Args:
        example_name: Name of the example
        example_config: Configuration dict with traces_dir, metrics_dir, base_dir
        mode: Analysis mode ('latest' or 'per_run')
        project_root: Project root directory
    """
    # Resolve paths relative to project root
    base_dir = example_config.get('base_dir')
    if base_dir:
        base_dir = project_root / base_dir if not Path(base_dir).is_absolute() else Path(base_dir)
    else:
        base_dir = project_root

    traces_dir = example_config.get('traces_dir', 'traces')
    metrics_dir = example_config.get('metrics_dir', 'metrics')

    # Resolve traces_dir and metrics_dir
    if not Path(traces_dir).is_absolute():
        traces_dir = str(base_dir / traces_dir)
    if not Path(metrics_dir).is_absolute():
        metrics_dir = str(base_dir / metrics_dir)

    # Output directory: plot/figures/<example_name>
    output_dir = _script_dir / 'figures' / example_name
    # Ensure output directory and all parent directories exist
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print(f"Processing example: {example_name}")
    print("=" * 80)
    print(f"Analysis mode: {mode}")
    print(f"Base directory: {base_dir}")
    print(f"Traces directory: {traces_dir}")
    print(f"Metrics directory: {metrics_dir}")
    print(f"Output directory: {output_dir}")
    print()

    dataset_example_name = example_config.get("dataset_example_name")
    tags = example_config.get("tags")
    if isinstance(tags, str):
        tags = [tags]

    # Create analyzer with specified parameters
    analyzer = MetricsAnalyzer(
        traces_dir=traces_dir,
        metrics_dir=metrics_dir,
        base_dir=str(base_dir),
        analysis_mode=mode,
        example_name=dataset_example_name or example_name,
        tags=tags,
    )

    # Run analysis
    analyzer.analyze(output_dir=str(output_dir))
    print(f"\n✓ Completed visualization for {example_name}")
    print(f"  Output saved to: {output_dir}\n")


def main():
    """Main function to generate all visualizations."""
    parser = argparse.ArgumentParser(
        description='Plot OpenTelemetry metrics for examples',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (Unified Mode - Recommended):
  # Use default config (plot/configs/all_examples_parquet_plot_config.json)
  python plot_example_metrics.py

  # Process a single example by name
  python plot_example_metrics.py --example brand-search-optimization \\
      --example-traces-dir examples/adk/brand-search-optimization/traces \\
      --example-metrics-dir examples/adk/brand-search-optimization/metrics

  # Process multiple examples from config file
  python plot_example_metrics.py --examples-config examples_config.json --mode per_run

  # Run latest + per_run in one command
  python plot_example_metrics.py --examples-config examples_config.json --mode all

  # Process single example with base directory
  python plot_example_metrics.py --example marketing-agency \\
      --example-base-dir examples/adk/marketing-agency \\
      --example-traces-dir traces \\
      --example-metrics-dir metrics

Legacy Mode (Backward Compatible):
  # Analyze latest run (quick analysis)
  python plot_example_metrics.py --mode latest

  # Analyze all runs with statistics (recommended for academic papers)
  python plot_example_metrics.py --mode per_run

  # Use custom directories
  python plot_example_metrics.py --traces-dir custom_traces --metrics-dir custom_metrics

  # Use configuration file
  python plot_example_metrics.py --config config.json
        """
    )

    # Unified mode arguments (new)
    parser.add_argument(
        '--example',
        type=str,
        default=None,
        help='Example name to process (unified mode). Requires --example-traces-dir and --example-metrics-dir, or use --examples-config for multiple examples.'
    )
    parser.add_argument(
        '--example-traces-dir',
        type=str,
        default=None,
        help='Traces directory for the example (relative to project root or absolute path). Used with --example.'
    )
    parser.add_argument(
        '--example-metrics-dir',
        type=str,
        default=None,
        help='Metrics directory for the example (relative to project root or absolute path). Used with --example.'
    )
    parser.add_argument(
        '--example-base-dir',
        type=str,
        default=None,
        help='Base directory for the example (relative to project root or absolute path). If specified, traces-dir and metrics-dir are relative to this. Used with --example.'
    )
    parser.add_argument(
        '--examples-config',
        type=str,
        default=None,
        help='Path to JSON file containing multiple examples configuration. Format: {"example_name": {"traces_dir": "...", "metrics_dir": "...", "base_dir": "..."}, ...}'
    )
    parser.add_argument(
        '--tree-of-thoughts-model-similarity',
        action='store_true',
        help='Generate tree-of-thoughts call graph similarity violins across models (hardcoded paths).'
    )

    # Legacy mode arguments (backward compatible)
    parser.add_argument(
        '--mode',
        choices=['latest', 'per_run', 'all'],
        default=None,
        help='Analysis mode: latest (quick analysis), per_run (per-run stats), or all (run latest + per_run). Default: per_run.'
    )
    parser.add_argument(
        '--traces-dir',
        type=str,
        default=None,
        help='Directory containing trace JSONL/JSON files (default: "traces" in current working directory). Can be "spans" or any custom directory name. (Legacy mode)'
    )
    parser.add_argument(
        '--metrics-dir',
        type=str,
        default=None,
        help='Directory containing metrics JSONL files (default: "metrics" in current working directory). (Legacy mode)'
    )
    parser.add_argument(
        '--base-dir',
        type=str,
        default=None,
        help='Base directory for traces-dir and metrics-dir (default: current working directory). (Legacy mode)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Output directory for visualization files (default: "visualizations"). (Legacy mode)'
    )
    parser.add_argument(
        '--config',
        type=str,
        default=None,
        help='Path to configuration JSON file. If specified, loads settings from file (command-line arguments override config file values). (Legacy mode)'
    )
    parser.add_argument(
        '--save-config',
        type=str,
        default=None,
        help='Save current command-line arguments to a configuration file for future use. (Legacy mode)'
    )

    args = parser.parse_args()

    if args.tree_of_thoughts_model_similarity:
        plot_tree_of_thoughts_model_similarity()
        return

    legacy_mode = _use_legacy_mode(args)
    if not legacy_mode and args.examples_config is None and args.example is None:
        if DEFAULT_EXAMPLES_CONFIG.exists():
            args.examples_config = str(DEFAULT_EXAMPLES_CONFIG)
        else:
            raise SystemExit(
                f"Default examples config not found: {DEFAULT_EXAMPLES_CONFIG}"
            )

    # Determine mode: unified or legacy
    is_unified_mode = not legacy_mode

    if is_unified_mode:
        # Unified mode: process examples
        mode = args.mode or 'all'
        modes_to_run = ['latest', 'per_run'] if mode == 'all' else [mode]

        if args.examples_config:
            # Process multiple examples from config file
            config_file = Path(args.examples_config)
            if not config_file.is_absolute():
                config_file = Path.cwd() / config_file
                if not config_file.exists():
                    config_file = _project_root / args.examples_config

            examples_config = parse_examples_config(config_file)
            print(f"Loaded examples configuration from {config_file}")
            print(f"Found {len(examples_config)} example(s) to process\n")

            # Process each example separately (supports running multiple modes)
            for example_name, example_config in examples_config.items():
                for run_mode in modes_to_run:
                    try:
                        process_example(example_name, example_config, run_mode, _project_root)
                    except Exception as e:
                        print(f"✗ Error processing {example_name} ({run_mode}): {e}\n")
                        continue

            print("=" * 80)
            print("All examples processed!")
            print("=" * 80)

        elif args.example:
            # Process single example
            if not args.example_traces_dir or not args.example_metrics_dir:
                parser.error("--example requires --example-traces-dir and --example-metrics-dir, or use --examples-config")

            example_config = {
                'traces_dir': args.example_traces_dir,
                'metrics_dir': args.example_metrics_dir,
                'base_dir': args.example_base_dir,
            }

            for run_mode in modes_to_run:
                process_example(args.example, example_config, run_mode, _project_root)
        else:
            parser.error("Must specify either --example or --examples-config for unified mode")

    else:
        # Legacy mode: backward compatible behavior
        # Load configuration from file if specified
        config = {}
        if args.config:
            config_file = Path(args.config)
            if not config_file.is_absolute():
                # If relative path, try relative to current directory first, then project root
                config_file = Path.cwd() / config_file
                if not config_file.exists():
                    config_file = _project_root / args.config
            config = load_config(config_file)
            if config:
                print(f"Loaded configuration from {config_file}")

        # Merge config with command-line arguments (CLI args take precedence)
        mode = args.mode or config.get('mode', 'all')
        modes_to_run = ['latest', 'per_run'] if mode == 'all' else [mode]
        traces_dir = args.traces_dir or config.get('traces_dir')
        metrics_dir = args.metrics_dir or config.get('metrics_dir')
        base_dir = args.base_dir or config.get('base_dir')
        output_dir = args.output_dir or config.get('output_dir', 'visualizations')

        # Save configuration if requested
        if args.save_config:
            save_config_file = Path(args.save_config)
            if not save_config_file.is_absolute():
                save_config_file = Path.cwd() / save_config_file

            config_to_save = {
                'mode': mode,
                'traces_dir': traces_dir,
                'metrics_dir': metrics_dir,
                'base_dir': base_dir,
                'output_dir': output_dir,
            }
            # Remove None values
            config_to_save = {k: v for k, v in config_to_save.items() if v is not None}
            save_config(config_to_save, save_config_file)

        print("=" * 80)
        print("OpenTelemetry Metrics Visualization (Legacy Mode)")
        print("=" * 80)
        print(f"Analysis mode: {mode}")
        if traces_dir:
            print(f"Traces directory: {traces_dir}")
        if metrics_dir:
            print(f"Metrics directory: {metrics_dir}")
        if base_dir:
            print(f"Base directory: {base_dir}")
        print(f"Output directory: {output_dir}")
        print()

        # Create analyzer with specified parameters
        for run_mode in modes_to_run:
            analyzer = MetricsAnalyzer(
                traces_dir=traces_dir,
                metrics_dir=metrics_dir,
                base_dir=base_dir,
                analysis_mode=run_mode
            )

            # Run analysis
            analyzer.analyze(output_dir=output_dir)


if __name__ == "__main__":
    main()
