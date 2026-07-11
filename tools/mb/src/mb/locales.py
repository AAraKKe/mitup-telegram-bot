from pathlib import Path

import typer

from . import runner

LOCALES_RELATIVE_DIR = Path("mitup_bot/locales")

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
    return runner.run_command(runner.uv("mitup", "translations", "build"))


def ensure_locales_built() -> int:
    """Build the .mo catalogs only when they are stale. Returns the build's exit code (0 if skipped)."""
    if not locales_stale(runner.repo_root() / LOCALES_RELATIVE_DIR):
        return 0
    return build_locales()


@app.command()
def build():
    """Compile .po sources into .mo catalogs."""
    raise typer.Exit(build_locales())


@app.command()
def sync():
    """Update the source catalog, drop stale entries, rebuild, and validate."""
    steps = (
        runner.uv("mitup", "translations", "update"),
        runner.uv("mitup", "translations", "clean-locales"),
        runner.uv("mitup", "translations", "build"),
        runner.uv("mitup", "translations", "validate-locales"),
    )
    for step in steps:
        exit_code = runner.run_command(step)
        if exit_code != 0:
            raise typer.Exit(exit_code)


@app.command()
def validate():
    """Validate that every locale catalog is complete and consistent."""
    raise typer.Exit(runner.run_command(runner.uv("mitup", "translations", "validate-locales")))


@app.command()
def clean():
    """Remove stale msgid blocks from non-English catalogs."""
    raise typer.Exit(runner.run_command(runner.uv("mitup", "translations", "clean-locales")))
