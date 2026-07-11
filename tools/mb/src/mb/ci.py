import typer

from . import runner

app = typer.Typer(no_args_is_help=True, help="CI checks (normally run by the pipeline).")

PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


@app.command("check-commit", context_settings=PASSTHROUGH)
def check_commit(ctx: typer.Context):
    """Validate commit message format."""
    raise typer.Exit(runner.run_command(runner.uv("python", "bin/check_commit_message.py", *ctx.args)))


@app.command("check-ty-ignores", context_settings=PASSTHROUGH)
def check_ty_ignores(ctx: typer.Context):
    """Validate that every ty suppression carries a GitHub issue URL."""
    raise typer.Exit(runner.run_command(runner.uv("python", "bin/check_ty_ignores.py", *ctx.args)))


@app.command("check-languages", context_settings=PASSTHROUGH)
def check_languages(ctx: typer.Context):
    """Validate the CI language matrix against the supported languages."""
    raise typer.Exit(runner.run_command(runner.uv("python", "bin/check_ci_languages.py", *ctx.args)))
