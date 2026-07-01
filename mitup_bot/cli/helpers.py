from rich.console import Console

__console = Console(width=90)


def console() -> Console:
    """Get the console instance. Can be mocked for testing to generate custom consoles."""
    return __console


def error(msg: str):
    console().print(f"[bold red]✘ {msg}[/]")


def success(msg: str):
    console().print(f"[bold green]✔︎ {msg}[/]")
