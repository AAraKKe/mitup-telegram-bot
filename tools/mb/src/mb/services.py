from typing import Annotated

import typer

from . import runner

run_app = typer.Typer(no_args_is_help=True, help="Run the bot or the recurrent-events worker.")
docker_app = typer.Typer(no_args_is_help=True, help="Docker compose lifecycle.")

# In production each app ships its own `mitup` console script (bot → launch, events →
# recurrent-events), but the dev workspace installs both into one shared venv where a single
# `mitup` on PATH is ambiguous. Invoking each app by its module keeps dev deterministic while the
# frozen container commands (`mitup launch` / `mitup recurrent-events`) stay valid per image.
BOT_MODULE = "mitup_bot.bot_cli"
EVENTS_MODULE = "mitup_bot.events_cli"

DEBUGPY_ARGS = [
    "python",
    "-Xfrozen_modules=off",
    "-m",
    "debugpy",
    "--listen",
    "0.0.0.0:5678",
    "--wait-for-client",
    "-m",
    BOT_MODULE,
    "launch",
]


@run_app.command()
def bot(
    debug: Annotated[bool, typer.Option("--debug", help="Wait for a debugpy client on port 5678.")] = False,
    docker: Annotated[bool, typer.Option("--docker", help="Run inside docker compose instead of locally.")] = False,
):
    """Start the bot."""
    if docker:
        service = "debug" if debug else "mitup"
        raise typer.Exit(runner.run_command(["docker", "compose", "up", service]))
    exit_code = runner.uv(*DEBUGPY_ARGS) if debug else runner.uv("python", "-m", BOT_MODULE, "launch")
    raise typer.Exit(exit_code)


@run_app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def events(
    ctx: typer.Context,
    docker: Annotated[bool, typer.Option("--docker", help="Run inside docker compose instead of locally.")] = False,
):
    """Start the recurrent-events worker (extra args pass through)."""
    if docker:
        raise typer.Exit(runner.run_command(["docker", "compose", "up", "events"]))
    raise typer.Exit(runner.uv("python", "-m", EVENTS_MODULE, "recurrent-events", *ctx.args))


@docker_app.command()
def up(services: Annotated[list[str] | None, typer.Argument(help="Services to start (default: all).")] = None):
    """Start compose services in the background."""
    raise typer.Exit(runner.run_command(["docker", "compose", "up", "-d", *(services or [])]))


@docker_app.command()
def down():
    """Stop and remove compose services."""
    raise typer.Exit(runner.run_command(["docker", "compose", "down"]))


@docker_app.command()
def logs(
    service: Annotated[str | None, typer.Argument(help="Service to show logs for (default: all).")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output.")] = False,
):
    """Show compose service logs."""
    command = ["docker", "compose", "logs"]
    if follow:
        command.append("--follow")
    if service:
        command.append(service)
    raise typer.Exit(runner.run_command(command))


@docker_app.command()
def ps(service: Annotated[str | None, typer.Argument(help="Service to show (default: all).")] = None):
    """List compose services."""
    command = ["docker", "compose", "ps"]
    if service:
        command.append(service)
    raise typer.Exit(runner.run_command(command))
