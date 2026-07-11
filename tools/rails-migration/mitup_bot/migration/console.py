from rich.console import Console

default_console = Console(width=90)


def console() -> Console:
    """Get the console instance. Can be mocked for testing to generate custom consoles."""
    return default_console


def error(msg: str):
    console().print(f"[bold red]✘ {msg}[/]")


def success(msg: str):
    console().print(f"[bold green]✔︎ {msg}[/]")
