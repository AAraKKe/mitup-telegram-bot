from typing import Annotated

import typer

from . import checks, ci, console, db, deploy_ops, docs, locales, release, services, setup_env, testing, validate

app = typer.Typer(
    name="mb",
    no_args_is_help=True,
    help="Developer CLI for the mitup-telegram-bot repository.",
    add_completion=False,
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
app.command("release")(release.release_command)

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


@app.command()
def deploy(
    migrations_image: Annotated[
        str | None, typer.Option("--migrations-image", help="Uri of the migrations lambda image.")
    ] = None,
    bot_image: Annotated[str | None, typer.Option("--bot-image", help="Uri of the bot image pushed to ECR.")] = None,
    alarm_action_image: Annotated[
        str | None, typer.Option("--alarm-action-image", help="Uri of the alarm action lambda image.")
    ] = None,
    events_image: Annotated[
        str | None,
        typer.Option(
            "--events-image",
            help="Uri of the recurrent-events image — the only image carrying the `mitup recurrent-events` command.",
        ),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Redeploy both ECS services onto their latest registered task definition without building a new "
            "one or running migrations — use to pick up an infra/task-definition change.",
        ),
    ] = False,
):
    """Deploy the bot: update the lambdas and roll out the ECS services."""
    if refresh:
        deploy_ops.refresh()
        return

    if migrations_image is None or bot_image is None or alarm_action_image is None or events_image is None:
        console.error(
            "--migrations-image, --bot-image, --alarm-action-image and --events-image are all required unless --refresh"
        )
        raise typer.Abort()

    deploy_ops.deploy(migrations_image, bot_image, alarm_action_image, events_image)


if __name__ == "__main__":
    app()
