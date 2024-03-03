from click.testing import CliRunner
from rich.console import Console

from mitup_bot.cli.commands import validate_migrations as validate

# Make console not export colors and styles for testing
validate.console = Console(force_interactive=False, force_terminal=False)


def test_valid_migrations():
    runner = CliRunner()

    with validate.console.capture() as capture:
        runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/valid"])

    output = capture.get()

    # Only appears one as the base migration
    assert output.count("rev1 Migration 1") == 1
    # Appears twice as the child of the base and its own
    assert output.count("rev2 Migration 2") == 2


def test_invalid_migrations_with_branching():
    runner = CliRunner()

    with validate.console.capture() as capture:
        result = runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/invalid_branches"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    output = capture.get()

    # Only appears one as the base migration
    assert output.count("rev1 Migration 1") == 1
    # Appears twice as the child of the base and its own
    assert output.count("rev2 Migration 2") == 2
    # Appears twice as the child of the base and its own
    assert output.count("rev3 Migration 3") == 2
    assert "Branching migrations found!" in output


def test_invalid_migrations_disconnected():
    runner = CliRunner()

    with validate.console.capture() as capture:
        result = runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/invalid_disconnected"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    output = capture.get()

    # The only output is the error since the code will fail when parsing revisions. No tree will be printed
    assert "Revision 'rev3' is referenced by another revision but it doesn't exist" in output
