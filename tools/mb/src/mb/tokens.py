from typing import Annotated

import typer

from . import tokens_ops

app = typer.Typer(no_args_is_help=True, help="Automation-token operations.")


@app.command()
def rotate(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the rotation plan without changing anything.")
    ] = False,
):
    """Rotate every registered token and refresh the CI/CD variable carrying it."""
    raise typer.Exit(tokens_ops.rotate_tokens(dry_run=dry_run))
