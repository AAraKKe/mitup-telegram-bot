"""Single owner of the mb CLI's look and feel.

Every user-facing line goes through this module: one stdout console, one stderr console,
shared status glyphs, step headers, the summary-table style, and spinners. Command modules
never call print() or build their own Console — a test enforces it.

Color and animations are independent axes:

| Condition                 | Color                        | Animations |
|---------------------------|------------------------------|------------|
| --plain / MB_PLAIN=1      | off (zero ANSI bytes)        | off        |
| NO_COLOR set              | off (rich honors it)         | TTY-only   |
| FORCE_COLOR set           | on, even piped / in CI       | TTY-only   |
| default                   | rich's terminal detection    | TTY-only   |

"TTY-only": animations run only when stdout is an interactive terminal and CI is unset —
they are never forced on, not even by FORCE_COLOR (CI logs render color fine but replay
spinner frames as garbage). --plain / MB_PLAIN is the manual full kill for file redirects;
precedence: explicit flag > NO_COLOR/FORCE_COLOR (color axis only) > auto-detection.

All of this governs mb's own lines only: subprocess output streams through verbatim, and
each tool decides its own color (the repo's .env deliberately sets FORCE_COLOR=1 so the
test suite exercises rich's colored path).
"""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.table import Table

GLYPH_PASS = "✓"
GLYPH_FAIL = "✗"
GLYPH_WARN = "!"
GLYPH_STEP = "▸"

FALSY_ENV_VALUES = ("", "0", "false", "no")

plain_active = False
animations_active = False
output = Console()
errors = Console(stderr=True)


def plain_requested(flag: bool | None = None) -> bool:
    """Resolve the full kill-switch: explicit flag > MB_PLAIN env > off."""
    if flag is not None:
        return flag
    env_value = os.environ.get("MB_PLAIN")
    if env_value is not None:
        return env_value.strip().lower() not in FALSY_ENV_VALUES
    return False


def animations_allowed() -> bool:
    """Animations need an interactive terminal; CI never gets them regardless of color."""
    return sys.stdout.isatty() and not os.environ.get("CI")


def configure(plain: bool | None = None):
    """(Re)build the shared consoles; the app callback calls this once per invocation.

    Plain mode pins force_terminal=False: no_color alone still emits attribute codes
    (bold/dim), and only a non-terminal console is guaranteed byte-identical text.
    Otherwise color is left to rich, which honors FORCE_COLOR and NO_COLOR natively.
    """
    global plain_active, animations_active, output, errors
    plain_active = plain_requested(plain)
    if plain_active:
        animations_active = False
        output = Console(no_color=True, force_terminal=False, highlight=False)
        errors = Console(stderr=True, no_color=True, force_terminal=False, highlight=False)
    else:
        animations_active = animations_allowed()
        output = Console()
        errors = Console(stderr=True)


configure()


def success(message: str):
    output.print(f"[bold green]{GLYPH_PASS}[/bold green] {message}")


def error(message: str):
    errors.print(f"[bold red]{GLYPH_FAIL} {message}[/bold red]")


def warn(message: str):
    errors.print(f"[bold yellow]{GLYPH_WARN} {message}[/bold yellow]")


def info(message: str):
    output.print(message)


def step(title: str):
    output.print(f"[bold cyan]{GLYPH_STEP}[/bold cyan] [bold]{title}[/bold]")


def command_echo(command_line: str, location: str | None = None):
    # Command lines may contain literal brackets (pytest -k patterns), so markup is off
    # and the dim style is applied out of band. *location* (a path relative to the repo
    # root) is appended when the command runs somewhere other than the root, so the two
    # `ty check` runs don't read as an accidental duplicate in the logs.
    suffix = f"  (in {location})" if location else ""
    output.print(f"$ {command_line}{suffix}", markup=False, highlight=False, style="dim")


def raw(text: str):
    """Print pre-formatted text verbatim: no markup interpretation, no highlighting."""
    output.print(text, markup=False, highlight=False)


def show(renderable: object):
    output.print(renderable)


def rule(title: str = ""):
    output.rule(title)


def styled_table(title: str) -> Table:
    return Table(title=title, title_style="bold", header_style="bold", border_style="dim")


def status_cell(passed: bool) -> str:
    if passed:
        return f"[green]{GLYPH_PASS} PASS[/green]"
    return f"[red]{GLYPH_FAIL} FAIL[/red]"


@contextmanager
def spinner(message: str) -> Iterator[None]:
    """Animated status for quiet, short steps; a plain one-liner when animations are off.

    Only wrap commands whose output is captured — a spinner over a streaming subprocess
    garbles both.
    """
    if not animations_active or not output.is_terminal:
        output.print(f"{message}…")
        yield
        return
    with output.status(f"{message}…"):
        yield
