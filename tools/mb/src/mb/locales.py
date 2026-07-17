from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from . import console, crowdin_mr_ops, crowdin_ops, locales_ops, runner

LOCALES_RELATIVE_DIR = Path("libs/core/mitup_bot/locales")

app = typer.Typer(no_args_is_help=True, help="Manage gettext locale catalogs.")


def locales_stale(locales_dir: Path) -> bool:
    """Return True when the compiled .mo catalogs are missing or out of date with their .po sources.

    Tests need compiled catalogs, but recompiling on every run wastes seconds — this lets
    `mb test` skip the build when the sources are unchanged. Freshness is decided by hashing the
    .po contents against a stamp written at compile time, not by comparing mtimes: a fresh git
    checkout (every CI job) restamps the .po files newer than the .mo catalogs restored from the
    build-translations artifact, which an mtime comparison would misread as stale.
    """
    po_files = sorted(locales_dir.glob("*.po"))
    if not po_files:
        return True
    for po_file in po_files:
        mo_files = list((locales_dir / po_file.stem / "LC_MESSAGES").glob("*.mo"))
        if not mo_files:
            return True
    stamp = locales_dir / locales_ops.LOCALE_STAMP_NAME
    if not stamp.exists():
        return True
    return stamp.read_text().strip() != locales_ops.po_content_hash(locales_dir)


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
def push(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would be uploaded without writing to Crowdin.")
    ] = False,
):
    """Push the English source catalog and Crowdin-missing translations to Crowdin."""
    raise typer.Exit(crowdin_ops.push_catalogs(dry_run=dry_run))


@app.command()
def pull(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change without touching the catalogs.")
    ] = False,
):
    """Pull approved Crowdin translations into the locale catalogs and recompile them."""
    raise typer.Exit(crowdin_ops.pull_catalogs(dry_run=dry_run))


@app.command("create-mr")
def create_mr():
    """Commit pulled translations and force-push the crowdin-translations MR branch (CI only)."""
    raise typer.Exit(crowdin_mr_ops.create_translations_mr())


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
