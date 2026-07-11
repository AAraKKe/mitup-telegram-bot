import json
from pathlib import Path

import pytest
from rich.prompt import Confirm

from mb import vscode


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(vscode.runner, "repo_root", lambda: tmp_path)
    (tmp_path / ".vscode").mkdir()
    return tmp_path


def test_template_points_at_the_uv_venv():
    assert vscode.SETTINGS_TEMPLATE["python.testing.pytestPath"] == ".venv/bin/pytest"
    files_exclude = vscode.SETTINGS_TEMPLATE["files.exclude"]
    assert isinstance(files_exclude, dict)
    assert ".docker_uv" in files_exclude


def test_apply_writes_settings_after_confirmation(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Confirm, "ask", staticmethod(lambda *args, **kwargs: True))

    assert vscode.apply_vscode_settings() == 0

    written = json.loads((workspace / ".vscode" / "settings.json").read_text())
    assert written["python.testing.pytestPath"] == ".venv/bin/pytest"


def test_apply_preserves_unrelated_existing_settings(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    (workspace / ".vscode" / "settings.json").write_text(json.dumps({"editor.rulers": [120]}))
    monkeypatch.setattr(Confirm, "ask", staticmethod(lambda *args, **kwargs: True))

    assert vscode.apply_vscode_settings() == 0

    written = json.loads((workspace / ".vscode" / "settings.json").read_text())
    assert written["editor.rulers"] == [120]
    assert written["python.testing.pytestEnabled"] is True


def test_apply_leaves_files_untouched_when_declined(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Confirm, "ask", staticmethod(lambda *args, **kwargs: False))

    assert vscode.apply_vscode_settings() == 0
    assert not (workspace / ".vscode" / "settings.json").exists()


def test_apply_reports_compatible_settings_without_prompting(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    proposed = dict(vscode.SETTINGS_TEMPLATE)
    (workspace / ".vscode" / "settings.json").write_text(json.dumps(proposed))

    def fail_if_asked(*args: object, **kwargs: object) -> bool:
        raise AssertionError("Confirm.ask must not be called when settings already match")

    monkeypatch.setattr(Confirm, "ask", staticmethod(fail_if_asked))

    assert vscode.apply_vscode_settings() == 0
