import click

from mitup_bot.app import MitupRuntime
from mitup_bot.config import Env


@click.group()
def cli():
    """Entry point for the bot application container (the ``mitup`` console script)."""


@cli.command()
@click.option("--env", default=Env.DEV, type=click.Choice(choices=Env, case_sensitive=False))
def launch(env: Env):
    MitupRuntime(env).run()


def main():
    cli()


if __name__ == "__main__":
    main()
