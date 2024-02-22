from pathlib import Path

import pytest
from click import Command, Context

from mitup_bot.cli.cli_commands import MitupCliCommand


def expected_command_names() -> list[str]:
    commands_path = Path(__file__).parent / "../../mitup_bot/cli/commands"
    return [command.stem for command in commands_path.glob("*.py") if command.stem != "__init__"]


@pytest.fixture(scope="session")
def cli_command() -> MitupCliCommand:
    return MitupCliCommand()


@pytest.fixture
def dummy_context() -> Context:
    return Context(Command("dummy"))


@pytest.mark.parametrize("command_name", expected_command_names())
def test_cli_commands_registers_commands(command_name: str, dummy_context: Context, cli_command: MitupCliCommand):
    assert command_name in cli_command.list_commands(dummy_context)


@pytest.mark.parametrize("command_name", expected_command_names())
def test_cli_commands_loads_commands_properly(command_name: str, dummy_context: Context, cli_command: MitupCliCommand):
    command = cli_command.get_command(dummy_context, command_name)

    assert command is not None
    assert command.callback is not None
    # The name of the command create is always cli, as that is the name of the method
    # even if we interact with them with the proper name
    assert command.name == "cli"
