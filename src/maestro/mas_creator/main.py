import argparse
import asyncio
import sys
from pathlib import Path

# Provide mas_creator to the path if not installed
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maestro.mas_creator.builder import GroupBuilder

EXIT_COMMANDS = {"exit", "quit", "q"}

async def create_mas(config_path: str | Path, tools_path: str | Path | None = None):
    """
    Create a multi-agent system (MAS) from a JSON config file and an optional tools file.

    Args:
        config_path: Path to the JSON configuration file for the multi-agent system.
        tools_path: Optional path to a Python file containing tool functions.

    Returns:
        An initialized multi-agent group instance (for example, RoundRobinGroup,
        StarGroup, or HandoffGroup).
    """
    print(f"Loading group from config: {config_path}")
    if tools_path:
        print(f"Loading tools from: {tools_path}")
    
    group = await GroupBuilder.build_from_config_async(config_path, tools_path)
    return group

async def main():
    parser = argparse.ArgumentParser(description="Create and run a Multi-Agent System from config files.")
    parser.add_argument(
        "config_path",
        type=str,
        help="Path to the JSON configuration file (e.g., config.json)"
    )
    parser.add_argument(
        "--tools",
        "-t",
        type=str,
        default=None,
        help="Optional path to the Python file containing tools (e.g., tools.py)"
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Optional initial task. If omitted, the program reads it from console input."
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Run in interactive multi-turn mode (type 'exit' to stop).",
    )
    
    args = parser.parse_args()
    
    config_path = Path(args.config_path)
    tools_path = Path(args.tools) if args.tools else None
    
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
        
    if tools_path and not tools_path.exists():
        print(f"Error: Tools file not found: {tools_path}", file=sys.stderr)
        sys.exit(1)

    group = await create_mas(config_path, tools_path)
    print(f"Success! Built group: {group.__class__.__name__}")

    if args.interactive:
        print("\nInteractive session started. Type 'exit' to stop.")
        pending_input = args.task
        while True:
            if pending_input is None:
                try:
                    user_input = input("\nUser: ").strip()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\nInterrupted.")
                    break
            else:
                user_input = pending_input.strip()
                pending_input = None

            if not user_input:
                continue
            if user_input.lower() in EXIT_COMMANDS:
                break

            print(f"\nTask: {user_input}\n{'-' * 60}")
            result = await group.run(user_input)
            print(f"\nAssistant:\n{result}")
        return

    task = args.task
    if task is None:
        try:
            task = input("Enter task: ").strip()
        except EOFError:
            print(
                "Error: No --task provided and no console input is available.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not task:
            print("Error: Task cannot be empty.", file=sys.stderr)
            sys.exit(1)

    print(f"\nTask: {task}\n{'-' * 60}")
    result = await group.run(task)
    
    print(f"\n{'=' * 60}\nFinal output:\n{result}")

if __name__ == "__main__":
    asyncio.run(main())
