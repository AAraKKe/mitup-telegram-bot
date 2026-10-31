import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from command_recording import CommandRecorder

from mb import console, runner


@pytest.fixture(autouse=True)
def reset_console() -> Iterator[None]:
    """CLI invocations reconfigure the global consoles; restore auto-detection after each test."""
    yield
    console.configure()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CommandRecorder:
    command_recorder = CommandRecorder()
    monkeypatch.setattr(runner, "run_command", command_recorder)
    monkeypatch.setattr(runner, "run_quiet", command_recorder.run_quiet)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    # Machines differ on whether docker is installed, so each test states it through `docker_on_path`.
    monkeypatch.setattr(shutil, "which", lambda _command: None)
    return command_recorder
