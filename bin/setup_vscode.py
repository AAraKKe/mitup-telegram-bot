import json
from pathlib import Path
from subprocess import CalledProcessError, check_output

from rich import print
from rich.prompt import Confirm

TSettingType = int | str | bool | float
TSettingValue = TSettingType | list[TSettingType] | dict[str, "TSettingValue"]


SETTINGS_TEMPLATE: dict[str, TSettingValue] = {
    "files.exclude": {"__pycache__": True},
    "python.testing.unittestEnabled": False,
    "python.testing.pytestEnabled": True,
    "python.analysis.inlayHints.pytestParameters": True,
    "python.testing.pytestPath": "XXX",
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
}


def hatch_dev_env() -> Path:
    try:
        return Path(check_output(["hatch", "env", "find", "dev"]).strip().decode())
    except CalledProcessError:
        print("[red]Error: Could not get environment 'dev' information.[/red]")
        raise
    except Exception:
        print("[red]Could't setup VSCode configuration due to an unkown error.[/red]")
        raise


def current_vscode_settings() -> dict[str, TSettingValue]:
    if (settings_path := Path(".vscode/settings.json")).exists():
        with open(settings_path) as settings_file:
            return json.load(settings_file)
    return {}


def generate_settings() -> dict[str, TSettingValue]:
    pytest_path = hatch_dev_env() / "bin/pytest"
    settings = SETTINGS_TEMPLATE.copy()

    try:
        settings["python.testing.pytestPath"] = str(pytest_path.relative_to(Path.cwd()))
    except ValueError:
        # Hatch path is not relative to workspace
        settings["python.testing.pytestPath"] = str(pytest_path)

    return settings


def print_compare(current: dict[str, TSettingValue], proposed: dict[str, TSettingValue]):
    # Need to transform values into strings because json can have dicts and lists as values
    # which are not hashable into a set
    current_set = {(k, str(v)) for k, v in current.items()}
    proposed_set = {(k, str(v)) for k, v in proposed.items()}
    diff = current_set ^ proposed_set

    if len(diff) == 0:
        print("Your current VSCode settings " "[bold green]are compatiblep[/bold green] " "with this project settigns!")
        exit(0)

    diff_dict = dict(diff)
    print("The following updates will be applied to this workspace VSCode settings:")
    diff_str = "{\n"
    for name, value in current.items():
        if name not in diff_dict:
            diff_str += f"    {name!r}: {value!r},\n"
        else:
            diff_str += f"  [bold red]- {name!r}: {value!r}[/bold red],\n"
            if name in proposed:
                diff_str += f"  [bold green]+ {name!r}: {proposed[name]!r}[/bold green],\n"

    for name, value in diff_dict.items():
        if name not in current:
            diff_str += f"  [bold green]+ {name!r}: {value!r}[/bold green],\n"

    diff_str += "}"
    print(diff_str)


def can_run_update(current: dict[str, TSettingValue], proposed_settings: dict[str, TSettingValue]) -> bool:
    print_compare(current, proposed_settings)
    return Confirm.ask("Do you want to apply these modifications?")


def main():
    if current_settings := current_vscode_settings():
        print("Found existing settigns in this workspace.")
    else:
        print("No previous settings have been found in this workspace.")

    # This is just a simple merge, we are not expecting this to be run after
    # the project is advanced and settings should be empty
    proposed_settings = current_settings | generate_settings()

    if can_run_update(current_settings, proposed_settings):
        with open(".vscode/settings.json", "w") as settings:
            try:
                settings.write(json.dumps(proposed_settings, indent=4))
                print("[bold green]VSCode settings have been updated for this workspace![/bold green]")
            except Exception:
                from rich.console import Console

                console = Console()
                console.print_exception(show_locals=True)
    else:
        print("No changes will be applied to your workspace settings.")


if __name__ == "__main__":
    main()
