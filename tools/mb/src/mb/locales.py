from collections.abc import Callable
from pathlib import Path

import typer

from . import console, locales_ops, runner

LOCALES_RELATIVE_DIR = Path("libs/core/mitup_bot/locales")

app = typer.Typer(no_args_is_help=True, help="Manage gettext locale catalogs.")


def locales_stale(locales_dir: Path) -> bool:
    """Return True when any compiled .mo catalog is missing or older than its .po source.

    Tests need compiled catalogs, but recompiling on every run wastes seconds — the
    mtime comparison lets `mb test` skip the build when nothing changed.
    """
    po_files = sorted(locales_dir.glob("*.po"))
    if not po_files:
        return True
    for po_file in po_files:
        mo_files = list((locales_dir / po_file.stem / "LC_MESSAGES").glob("*.mo"))
        if not mo_files:
            return True
        if any(mo_file.stat().st_mtime < po_file.stat().st_mtime for mo_file in mo_files):
            return True
    return False


def build_locales() -> int:
    return locales_ops.compile_locales()


def ensure_locales_built() -> int:
    """Build the .mo catalogs only when they are stale. Returns the build's exit code (0 if skipped)."""
    if not locales_stale(runner.repo_root() / LOCALES_RELATIVE_DIR):
        return 0
    return build_locales()


def update_source_catalog() -> int:
    locales_ops.generate_translations(validate=False)
    console.success("English source catalog updated.")
    return 0


@app.command()
def build():
    """Compile .po sources into .mo catalogs."""
    raise typer.Exit(locales_ops.compile_locales())


@app.command("update-source")
def update_source():
    """Regenerate the English source catalog from the message definitions in code."""
    raise typer.Exit(update_source_catalog())


@app.command("validate-ids")
def validate_ids():
    """Ensure every message in code has an entry in the English source catalog."""
    locales_ops.generate_translations(validate=True)
    raise typer.Exit(locales_ops.validate_translations())


@app.command()
def validate():
    """Validate that every locale catalog carries the same msgids as English."""
    raise typer.Exit(locales_ops.ensure_all_translations())


@app.command()
def clean():
    """Remove stale msgid blocks from non-English catalogs."""
    raise typer.Exit(locales_ops.clean_all_locales())


@app.command()
def sync():
    """Update the source catalog, drop stale entries, rebuild, and validate."""
    steps: tuple[tuple[str, Callable[[], int]], ...] = (
        ("Updating source catalog", update_source_catalog),
        ("Removing stale entries", locales_ops.clean_all_locales),
        ("Compiling locale catalogs", locales_ops.compile_locales),
        ("Validating locale catalogs", locales_ops.ensure_all_translations),
    )
    for index, (title, step) in enumerate(steps, start=1):
        console.step(f"({index}/{len(steps)}) {title}")
        exit_code = step()
        if exit_code != 0:
            console.error(f"{title} failed (exit code {exit_code}).")
            raise typer.Exit(exit_code)
    console.success("Locales synced.")
