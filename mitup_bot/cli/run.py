from mitup_bot.cli.cli_commands import MitupCliCommand


def main():
    """This custom command class reads every file under the commands folder and creates
    a command with the name of the file. Each file must contain a method named cli that
    is decorated with a click.command decorator.
    """
    MitupCliCommand(no_args_is_help=True)()


if __name__ == "__main__":
    main()
