from pathlib import Path

import pytest
from command_recording import CommandRecorder
from mb import runner


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CommandRecorder:
    command_recorder = CommandRecorder()
    monkeypatch.setattr(runner, "run_command", command_recorder)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    return command_recorder
