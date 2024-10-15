from rich.console import Console


def text_with_ansi_codes(text: str) -> str:
    """Renders the given text with the specified style as an ANSI escaped string."""
    console = Console(record=True, width=90)
    console.print(f"{text}")
    return console.export_text(clear=True, styles=True)


def rule(message: str) -> str:
    """Returns a ruler with the given message."""
    console = Console(record=True, width=90)
    console.rule(message)
    return console.export_text(clear=True, styles=True)
