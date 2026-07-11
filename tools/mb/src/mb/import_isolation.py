from pathlib import Path

from . import runner

# Representative imports per package: importing these exercises the module's whole runtime
# import graph, so success proves the package's declared dependencies are a sufficient closure.
CORE_IMPORTS = "import mitup_bot.config; import mitup_bot.supporter; import mitup_bot.translations"
MONITORING_IMPORTS = "import mitup_bot.monitoring"
DATA_IMPORTS = "import mitup_bot.models; import mitup_bot.db"
TELEGRAM_IMPORTS = "import mitup_bot.api_wrapper; import mitup_bot.views; import mitup_bot.utils"


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
    data_path = str(repo_root / "libs/data")
    telegram_path = str(repo_root / "libs/telegram")
    core_exit = check_package("mitup-core", [core_path], CORE_IMPORTS)
    # mitup-monitoring declares mitup-core as a workspace dependency; the isolated environment has
    # no workspace, so core is supplied by path to stand in for that declared edge.
    monitoring_exit = check_package("mitup-monitoring", [monitoring_path, core_path], MONITORING_IMPORTS)
    # mitup-data declares mitup-core and mitup-monitoring as workspace dependencies, supplied by path
    # here. python-telegram-bot is deliberately absent: importing the models and the engine proves the
    # persistence layer is PTB-free, which is what lets the migrations Lambda drop the PTB dependency.
    data_exit = check_package("mitup-data", [data_path, core_path, monitoring_path], DATA_IMPORTS)
    # mitup-telegram declares mitup-core, mitup-data and mitup-monitoring as workspace dependencies,
    # supplied by path here; python-telegram-bot is declared by the package itself, so it is present.
    # The proof is that importing the api wrapper, the views and the message utilities pulls in nothing
    # from the app-side guards/handlers layer — the telegram rendering layer depends only on the libs.
    telegram_exit = check_package(
        "mitup-telegram",
        [telegram_path, core_path, data_path, monitoring_path],
        TELEGRAM_IMPORTS,
    )
    return core_exit or monitoring_exit or data_exit or telegram_exit
