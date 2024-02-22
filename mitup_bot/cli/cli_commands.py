import importlib
from pathlib import Path

import click


class MitupCliCommand(click.MultiCommand):
    def list_commands(self, ctx: click.Context) -> list[str]:
        # The list of command names supported is obtained from the filename
        # of any python code in the commands folder
        commands_folder = Path(__file__).parent / "commands"
        return [
            command_file.stem for command_file in commands_folder.glob("*.py") if command_file.name != "__init__.py"
        ]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # The command itself should be stored in a method named cli
        module = importlib.import_module(f"mitup_bot.cli.commands.{cmd_name}")
        return getattr(module, "cli", None)
