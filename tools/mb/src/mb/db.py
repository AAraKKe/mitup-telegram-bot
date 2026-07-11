from typing import Annotated

import typer

from . import runner

app = typer.Typer(no_args_is_help=True, help="Local database lifecycle.")
migrate_app = typer.Typer(no_args_is_help=True, help="Alembic migrations.")
app.add_typer(migrate_app, name="migrate")


@app.command()
def up():
    """Start the local Postgres container and wait until it is healthy."""
    raise typer.Exit(runner.run_command(["docker", "compose", "up", "-d", "--wait", "postgres"]))


@migrate_app.command("up")
def migrate_up(revision: Annotated[str, typer.Argument(help="Target revision (default: head).")] = "head"):
    """Upgrade the database schema."""
    raise typer.Exit(runner.run_command(runner.uv("alembic", "upgrade", revision)))


@migrate_app.command("down")
def migrate_down(steps: Annotated[int, typer.Argument(min=1, help="Number of revisions to roll back.")] = 1):
    """Downgrade the database schema."""
    raise typer.Exit(runner.run_command(runner.uv("alembic", "downgrade", f"-{steps}")))


@migrate_app.command("new")
def migrate_new(name: Annotated[str, typer.Argument(help="Migration slug/description.")]):
    """Create an empty migration scaffold."""
    raise typer.Exit(runner.run_command(runner.uv("alembic", "revision", "-m", name)))


@migrate_app.command("validate")
def migrate_validate():
    """Validate the migration graph (single head, clean upgrade path)."""
    raise typer.Exit(runner.run_command(runner.uv("mitup", "validate-migrations")))
