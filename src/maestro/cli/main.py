import click
from pathlib import Path

@click.group()
@click.version_option()
def cli():
    pass

@cli.command()
@click.argument('example_path', type=click.Path(exists=True, path_type=Path))
def run(example_path: Path):
    """Run an example from the specified path.

    Args:
        example_path: Path to the example directory or file to run
    """
    click.echo(f"Running example at: {example_path}")
    # TODO: Implement run logic
    raise NotImplementedError("Run command not yet implemented")
