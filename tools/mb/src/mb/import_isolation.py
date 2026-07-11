from pathlib import Path

from . import runner

# Representative imports per package: importing these exercises the module's whole runtime
# import graph, so success proves the package's declared dependencies are a sufficient closure.
CORE_IMPORTS = "import mitup_bot.config; import mitup_bot.supporter; import mitup_bot.translations"
MONITORING_IMPORTS = "import mitup_bot.monitoring"


def check_package(name: str, package_paths: list[str], import_statement: str) -> int:
    """Import *import_statement* in an ephemeral environment built from *package_paths* only.

    `--isolated --no-project` ignores the shared workspace venv (which carries every member's
    dependencies) and resolves a fresh environment from the given paths alone, so an accidental
    reliance on an undeclared dependency surfaces as an ImportError instead of passing silently.
    """
    with_args = [argument for path in package_paths for argument in ("--with", path)]
    argv = ["uv", "run", "--isolated", "--no-project", *with_args, "python", "-c", import_statement]
    return runner.run_step(f"Import isolation: {name}", argv)


def run_check(repo_root: Path) -> int:
    core_path = str(repo_root / "libs/core")
    monitoring_path = str(repo_root / "libs/monitoring")
    core_exit = check_package("mitup-core", [core_path], CORE_IMPORTS)
    # mitup-monitoring declares mitup-core as a workspace dependency; the isolated environment has
    # no workspace, so core is supplied by path to stand in for that declared edge.
    monitoring_exit = check_package("mitup-monitoring", [monitoring_path, core_path], MONITORING_IMPORTS)
    return core_exit or monitoring_exit
