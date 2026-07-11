from typing import Annotated

import typer

from . import checks, ci, console, db, docs, locales, runner, services, setup_env, testing, validate

app = typer.Typer(
    name="mb",
    no_args_is_help=True,
    help="Developer CLI for the mitup-telegram-bot repository.",
)


@app.callback()
def configure_output(
    plain: Annotated[
        bool | None,
        typer.Option(
            "--plain/--no-plain",
            help="Kill all colors and animations (zero ANSI bytes; for file redirects). MB_PLAIN=1 is "
            "equivalent. Animations are TTY-only anyway; color follows FORCE_COLOR/NO_COLOR.",
        ),
    ] = None,
):
    console.configure(plain=plain)


app.command(
    "test",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)(testing.test_command)
app.command("validate")(validate.validate_command)
app.command("setup")(setup_env.setup_command)

app.add_typer(db.app, name="db")
app.add_typer(services.run_app, name="run")
app.add_typer(services.docker_app, name="docker")
app.add_typer(locales.app, name="locales")
app.add_typer(docs.app, name="docs")
app.add_typer(ci.app, name="ci", hidden=True)


@app.command()
def fix():
    """Format the code and apply all safe and unsafe lint fixes."""
    raise typer.Exit(checks.run_fix())


@app.command()
def lint(fix_issues: Annotated[bool, typer.Option("--fix", help="Apply fixes instead of only reporting.")] = False):
    """Run the linter."""
    raise typer.Exit(checks.run_lint(fix=fix_issues))


@app.command("format")
def format_code(
    check: Annotated[bool, typer.Option("--check", help="Report formatting diffs without writing.")] = False,
):
    """Run the formatter."""
    raise typer.Exit(checks.run_format(check=check))


@app.command()
def typecheck():
    """Type-check the project and the mb tool."""
    raise typer.Exit(checks.run_typecheck())


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def deploy(ctx: typer.Context):
    """Deploy the bot (extra args pass through)."""
    raise typer.Exit(runner.uv("mitup", "deploy", *ctx.args))


if __name__ == "__main__":
    app()
