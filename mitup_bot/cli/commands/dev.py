from pathlib import Path
from string import Template
from textwrap import dedent

import click

from mitup_bot.cli.helpers import success

DEV_CONFIG = dedent("""
    [bot]
    token = "${telegram_token}"

    [db]
    username = "mitupbot"
    database = "mitup"
    password = "12345pass"
    url      = "postgres" # "localhost"
    port     = 5432
    engine_echo = true

    [app]
    run_mode = "polling"

    [google_api]
    # Google API configuration
    gmaps_geocode_key = ""
    gmaps_timezone_key = ""

    [metrics]
    namespace = "MitupBot"
    environment = "rich"
    """)


@click.command()
@click.argument("token", type=str)
def cli(token: str):
    """
    Configure the development bot.

    This command will create a configuration file for the development bot.
    """
    dev_toml_path = Path(__file__).parents[2] / "environments" / "dev.toml"
    with open(dev_toml_path, "w") as f:
        f.write(Template(DEV_CONFIG).safe_substitute(telegram_token=token)[1:])

    success(f"Configuration file created at {dev_toml_path}")
