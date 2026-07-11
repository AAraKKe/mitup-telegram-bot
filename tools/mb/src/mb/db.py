from typing import Annotated

import typer

from . import migrate_ops, runner

app = typer.Typer(no_args_is_help=True, help="Local database lifecycle.")
migrate_app = typer.Typer(no_args_is_help=True, help="Alembic migrations.")
app.add_typer(migrate_app, name="migrate")


@app.command()
def up():
    """Start the local Postgres container and wait until it is healthy."""
    start_command = ["docker", "compose", "up", "-d", "--wait", "postgres"]
    raise typer.Exit(runner.run_step("Starting Postgres (docker compose)", start_command))


@migrate_app.command("up")
def migrate_up(revision: Annotated[str, typer.Argument(help="Target revision (default: head).")] = "head"):
    """Upgrade the database schema."""
    raise typer.Exit(runner.uv("alembic", "upgrade", revision))


@migrate_app.command("down")
def migrate_down(steps: Annotated[int, typer.Argument(min=1, help="Number of revisions to roll back.")] = 1):
    """Downgrade the database schema."""
    raise typer.Exit(runner.uv("alembic", "downgrade", f"-{steps}"))


@migrate_app.command("new")
def migrate_new(name: Annotated[str, typer.Argument(help="Migration slug/description.")]):
    """Create an empty migration scaffold."""
    raise typer.Exit(runner.uv("alembic", "revision", "-m", name))


@migrate_app.command("validate")
def migrate_validate(
    revisions_path: Annotated[
        str | None, typer.Option("-p", "--revisions-path", help="Path to the folder containing alembic revisions.")
    ] = None,
):
    """Validate the migration graph (single head, clean upgrade path)."""
    raise typer.Exit(migrate_ops.validate_migration_graph(revisions_path))
