import typer

from . import docs_ops, runner

app = typer.Typer(no_args_is_help=True, help="Documentation site.")


@app.command()
def serve():
    """Serve the docs locally with live reload."""
    raise typer.Exit(runner.uv("zensical", "serve"))


@app.command()
def build():
    """Build the static docs site."""
    raise typer.Exit(runner.uv("zensical", "build"))


@app.command()
def publish():
    """Sync the built docs site to S3 and invalidate the CloudFront cache."""
    docs_ops.publish_docs()
