from mitup_bot.cli import helpers
from tests.helpers import console


def test_success_formatter():
    with helpers.console().capture() as capture:
        helpers.success("Success!")
    assert capture.get() == console.text_with_ansi_codes("[bold green]✔︎ Success![/]")


def test_error_formatter():
    with helpers.console().capture() as capture:
        helpers.error("Error!")
    assert capture.get() == console.text_with_ansi_codes("[bold red]✘ Error![/]")
