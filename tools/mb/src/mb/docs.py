import typer

from . import runner

app = typer.Typer(no_args_is_help=True, help="Documentation site.")


@app.command()
def serve():
    """Serve the docs locally with live reload."""
    raise typer.Exit(runner.run_command(runner.uv("zensical", "serve")))


@app.command()
def build():
    """Build the static docs site."""
    raise typer.Exit(runner.run_command(runner.uv("zensical", "build")))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def publish(ctx: typer.Context):
    """Publish the docs site (extra args pass through)."""
    raise typer.Exit(runner.run_command(runner.uv("mitup", "publish-docs", *ctx.args)))
