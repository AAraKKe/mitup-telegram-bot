from pathlib import Path

import pytest
from command_recording import CommandRecorder
from mb import console, runner
from mb.main import app
from typer.testing import CliRunner

cli = CliRunner()


def test_help_hides_the_ci_group(recorder: CommandRecorder):
    result = cli.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert " ci " not in result.output


def test_ci_group_is_still_invokable(recorder: CommandRecorder):
    result = cli.invoke(app, ["ci", "check-ty-ignores"])

    assert result.exit_code == 0
    # The check runs natively (the recorder's tmp repo root has no sources to scan)
    assert recorder.commands == []


def test_lint_defaults_to_plain_ruff_check(recorder: CommandRecorder):
    result = cli.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert recorder.commands == [["uv", "run", "ruff", "check", "."]]


def test_lint_fix_uses_unsafe_fixes(recorder: CommandRecorder):
    cli.invoke(app, ["lint", "--fix"])

    assert recorder.commands == [["uv", "run", "ruff", "check", "--fix", "--unsafe-fixes", "."]]


def test_fix_formats_then_lints(recorder: CommandRecorder):
    cli.invoke(app, ["fix"])

    assert recorder.commands == [
        ["uv", "run", "ruff", "format", "."],
        ["uv", "run", "ruff", "check", "--fix", "--unsafe-fixes", "."],
    ]


def test_typecheck_echoes_version_then_covers_root_and_mb(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    recorder.captured_outputs["ty version"] = "ty 9.9.9 (abcdef0 2026-07-11)"
    printed: list[str] = []
    monkeypatch.setattr(console, "info", printed.append)

    cli.invoke(app, ["typecheck"])

    assert recorder.commands == [
        ["uv", "run", "ty", "version"],
        ["uv", "run", "ty", "check"],
        ["uv", "run", "ty", "check"],
    ]
    assert recorder.calls[1].cwd is None
    assert recorder.calls[2].cwd is not None
    assert recorder.calls[2].cwd.name == "mb"
    # The captured `ty version` output must reach the console, or CI loses the record of
    # which ty build produced the result even though `ty version` still runs.
    assert "ty 9.9.9 (abcdef0 2026-07-11)" in printed


def test_migrate_down_defaults_to_one_step(recorder: CommandRecorder):
    cli.invoke(app, ["db", "migrate", "down"])

    assert recorder.commands == [["uv", "run", "alembic", "downgrade", "-1"]]


def test_migrate_down_accepts_a_step_count(recorder: CommandRecorder):
    cli.invoke(app, ["db", "migrate", "down", "3"])

    assert recorder.commands == [["uv", "run", "alembic", "downgrade", "-3"]]


def test_migrate_up_defaults_to_head(recorder: CommandRecorder):
    cli.invoke(app, ["db", "migrate", "up"])

    assert recorder.commands == [["uv", "run", "alembic", "upgrade", "head"]]


def test_db_up_waits_for_health(recorder: CommandRecorder):
    cli.invoke(app, ["db", "up"])

    assert recorder.commands == [["docker", "compose", "up", "-d", "--wait", "postgres"]]


def test_run_bot_local(recorder: CommandRecorder):
    cli.invoke(app, ["run", "bot"])

    assert recorder.commands == [["uv", "run", "python", "-m", "mitup_bot.bot_cli", "launch"]]


def test_run_bot_debug_uses_debugpy(recorder: CommandRecorder):
    cli.invoke(app, ["run", "bot", "--debug"])

    assert recorder.commands[0][:6] == ["uv", "run", "python", "-Xfrozen_modules=off", "-m", "debugpy"]
    assert recorder.commands[0][-1] == "launch"


def test_run_bot_docker_uses_compose_service(recorder: CommandRecorder):
    cli.invoke(app, ["run", "bot", "--docker"])

    assert recorder.commands == [["docker", "compose", "up", "mitup"]]


def test_test_command_forwards_flags_and_passthrough(recorder: CommandRecorder):
    result = cli.invoke(app, ["test", "tests/utils", "--lang", "es_ES", "-k", "menu"])

    assert result.exit_code == 0
    pytest_call = recorder.commands[-1]
    assert pytest_call[:3] == ["uv", "run", "pytest"]
    assert pytest_call[-5:] == ["--lang", "es_ES", "tests/utils", "-k", "menu"]


def test_test_command_rejects_cov_with_db(recorder: CommandRecorder):
    result = cli.invoke(app, ["test", "--cov", "--db"])

    assert result.exit_code == 2
    assert recorder.commands == []


def test_location_note_is_none_at_repo_root():
    root = Path("/repo")
    assert runner.location_note(root, root) is None


def test_location_note_is_relative_for_a_subdirectory():
    root = Path("/repo")
    assert runner.location_note(root / "tools" / "mb", root) == "tools/mb"


def test_run_step_reports_success_and_hides_output(recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]):
    console.configure(plain=True)
    recorder.captured_outputs["good-cmd"] = "noisy tool output"

    assert runner.run_step("Good step", ["good-cmd"]) == 0

    captured = capsys.readouterr()
    assert f"{console.GLYPH_PASS} Good step" in captured.out
    assert "noisy tool output" not in captured.out


def test_run_step_dumps_captured_output_on_failure(recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]):
    console.configure(plain=True)
    recorder.exit_codes["bad-cmd"] = 3
    recorder.captured_outputs["bad-cmd"] = "detailed failure log"

    assert runner.run_step("Bad step", ["bad-cmd"]) == 3

    captured = capsys.readouterr()
    assert "detailed failure log" in captured.out
    assert "Bad step failed (exit code 3)" in captured.err
