from rich.console import Console

console = Console(width=90)


def error(msg: str):
    console.print(f"[bold red]✘ {msg}[/]")


def success(msg: str):
    console.print(f"[bold green]✔︎ {msg}[/]")
