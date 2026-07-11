import json
from pathlib import Path

from rich.prompt import Confirm

from . import console, runner

TSettingType = int | str | bool | float
TSettingValue = TSettingType | list[TSettingType] | dict[str, "TSettingValue"]


SETTINGS_TEMPLATE: dict[str, TSettingValue] = {
    "files.exclude": {"__pycache__": True, ".*_cache": True, ".docker_uv": True},
    "python.testing.unittestEnabled": False,
    "python.testing.pytestEnabled": True,
    "python.languageServer": "Default",
    "python.analysis.inlayHints.pytestParameters": True,
    # uv always places the project venv at .venv in the repo root
    "python.testing.pytestPath": ".venv/bin/pytest",
    "editor.tabSize": 4,
    "python.analysis.autoFormatStrings": True,
    "pythonIndent.trimLinesWithOnlyWhitespace": True,
    "coverage-gutters.coverageFileNames": ["coverage.xml"],
    "editor.formatOnSave": True,
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.codeActionsOnSave": {
            "source.fixAll": True,
            "source.organizeImports": True,
        },
    },
    "search.exclude": {"postgres-data": True},
    "ty.disableLanguageServices": False,
    "ty.diagnosticMode": "workspace",
}


def settings_path() -> Path:
    return runner.repo_root() / ".vscode" / "settings.json"


def current_vscode_settings() -> dict[str, TSettingValue]:
    if (path := settings_path()).exists():
        return json.loads(path.read_text())
    return {}


def settings_diff(current: dict[str, TSettingValue], proposed: dict[str, TSettingValue]) -> dict[str, str]:
    # Values are stringified because json can hold dicts and lists, which are not hashable in a set
    current_set = {(name, str(value)) for name, value in current.items()}
    proposed_set = {(name, str(value)) for name, value in proposed.items()}
    return dict(current_set ^ proposed_set)


def print_compare(current: dict[str, TSettingValue], proposed: dict[str, TSettingValue], diff: dict[str, str]):
    console.info("The following updates will be applied to this workspace VSCode settings:")
    diff_str = "{\n"
    for name, value in current.items():
        if name not in diff:
            diff_str += f"    {name!r}: {value!r},\n"
        else:
            diff_str += f"  [bold red]- {name!r}: {value!r}[/bold red],\n"
            if name in proposed:
                diff_str += f"  [bold green]+ {name!r}: {proposed[name]!r}[/bold green],\n"

    for name, value in diff.items():
        if name not in current:
            diff_str += f"  [bold green]+ {name!r}: {value!r}[/bold green],\n"

    diff_str += "}"
    console.show(diff_str)


def apply_vscode_settings() -> int:
    """Merge the project settings template into .vscode/settings.json, after confirmation."""
    if current_settings := current_vscode_settings():
        console.info("Found existing settings in this workspace.")
    else:
        console.info("No previous settings have been found in this workspace.")

    proposed_settings = current_settings | SETTINGS_TEMPLATE
    diff = settings_diff(current_settings, proposed_settings)

    if not diff:
        console.success("Your current VSCode settings are compatible with this project!")
        return 0

    print_compare(current_settings, proposed_settings, diff)
    if not Confirm.ask("Do you want to apply these modifications?"):
        console.info("No changes will be applied to your workspace settings.")
        return 0

    settings_path().write_text(json.dumps(proposed_settings, indent=4))
    console.success("VSCode settings have been updated for this workspace!")
    return 0
