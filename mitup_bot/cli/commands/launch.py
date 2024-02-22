import click

from mitup_bot.app import MitupRuntime
from mitup_bot.cli.options import EnumChoice, Env


@click.command()
@click.option("--env", default=Env.DEV, type=EnumChoice(Env))
@click.pass_context
def cli(ctx: click.Context, env: Env):
    MitupRuntime(env).run()
