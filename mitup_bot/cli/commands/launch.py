import click

from mitup_bot.app import MitupRuntime
from mitup_bot.config import Env


@click.command()
@click.option("--env", default=Env.DEV, type=click.Choice(choices=Env, case_sensitive=False))
def cli(env: Env):
    MitupRuntime(env).run()
