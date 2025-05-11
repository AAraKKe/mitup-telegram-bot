import importlib
from functools import reduce
from pathlib import Path

import click


def format(name: str, to_command=True) -> str:
    replacements: list[tuple[str, str]] = [("_", "-")]

    replaces = replacements if to_command else [tuple(reversed(replaces)) for replaces in replacements]

    return reduce(lambda name, rpl: name.replace(rpl[0], rpl[1]), replaces, name)


def file_to_command(name: str) -> str:
    return format(name)


def command_to_file(name: str) -> str:
    return format(name, to_command=False)


class MitupCliCommand(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        # The list of command names supported is obtained from the filename
        # of any python code in the commands folder
        commands_folder = Path(__file__).parent / "commands"
        return [
            file_to_command(command_file.stem)
            for command_file in commands_folder.glob("*.py")
            if command_file.name not in {"__init__.py"}
        ]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # The command itself should be stored in a method named cli
        module = importlib.import_module(f"mitup_bot.cli.commands.{command_to_file(cmd_name)}")
        return getattr(module, "cli", None)
