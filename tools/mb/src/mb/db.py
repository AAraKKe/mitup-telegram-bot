from typing import Annotated

import typer

from . import compose, migrate_ops, runner

app = typer.Typer(no_args_is_help=True, help="Local database lifecycle.")
migrate_app = typer.Typer(no_args_is_help=True, help="Alembic migrations.")
app.add_typer(migrate_app, name="migrate")

# Credentials for the local Postgres container; docker-compose.yaml is the source of truth.
LOCAL_DB_USER = "mitupbot"
LOCAL_DB_NAME = "mitup"

RESET_SCHEMA_SQL = "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"


def compose_alembic(*args: str) -> list[str]:
    """Argv running alembic inside the compose network via the migrations service.

    The service carries the in-network DB env vars, so migrations work from any checkout or
    worktree no matter where its dev.toml points the database: a dev.toml written for in-compose
    runs carries the service host "postgres", which does not resolve from the host.
    """
    return ["docker", "compose", "run", "--rm", "migrations-upgrade", *runner.uv_argv("alembic", *args)]


@app.command()
def up():
    """Start the local Postgres container and wait until it is healthy."""
    start_command = ["docker", "compose", "up", "-d", "--wait", "postgres"]
    raise typer.Exit(runner.run_step("Starting Postgres (docker compose)", start_command))


@app.command()
def reset(yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False):
    """Wipe the local database and rebuild the schema from migrations."""
    if not yes:
        typer.confirm("This drops every table in the local database. Continue?", abort=True)
    drop_command = [
        "docker",
        "compose",
        "exec",
        "postgres",
        "psql",
        "-U",
        LOCAL_DB_USER,
        "-d",
        LOCAL_DB_NAME,
        "-c",
        RESET_SCHEMA_SQL,
    ]
    exit_code = runner.run_step("Dropping the local database schema", drop_command)
    if exit_code != 0:
        raise typer.Exit(exit_code)
    compose.ensure_uv_cache_volume()
    raise typer.Exit(runner.run_command(compose_alembic("upgrade", "head")))


@migrate_app.command("up")
def migrate_up(revision: Annotated[str, typer.Argument(help="Target revision (default: head).")] = "head"):
    """Upgrade the database schema."""
    compose.ensure_uv_cache_volume()
    raise typer.Exit(runner.run_command(compose_alembic("upgrade", revision)))


@migrate_app.command("down")
def migrate_down(steps: Annotated[int, typer.Argument(min=1, help="Number of revisions to roll back.")] = 1):
    """Downgrade the database schema."""
    compose.ensure_uv_cache_volume()
    raise typer.Exit(runner.run_command(compose_alembic("downgrade", f"-{steps}")))


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
