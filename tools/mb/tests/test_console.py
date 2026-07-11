import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from command_recording import CommandRecorder
from mb import console
from mb.main import app
from typer.testing import CliRunner

cli = CliRunner()

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BARE_PRINT = re.compile(r"(?<![\w.])print\(")
CONSOLE_CONSTRUCTION = re.compile(r"\bConsole\(")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def clear_mode_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("MB_PLAIN", "CI", "NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)


# --- ownership: console.py is the only output module ---


def test_console_module_owns_all_output():
    package_dir = Path(console.__file__).parent
    offenders = [
        f"{module_path.name}:{line_number}: {line.strip()}"
        for module_path in sorted(package_dir.glob("*.py"))
        if module_path.name != "console.py"
        for line_number, line in enumerate(module_path.read_text().splitlines(), start=1)
        if BARE_PRINT.search(line.split("#", 1)[0]) or CONSOLE_CONSTRUCTION.search(line.split("#", 1)[0])
    ]
    assert not offenders, "print()/Console() outside console.py:\n" + "\n".join(offenders)


# --- kill-switch resolution: explicit flag > MB_PLAIN > off (never auto) ---


def test_explicit_flag_beats_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MB_PLAIN", "0")
    assert console.plain_requested(True) is True
    monkeypatch.setenv("MB_PLAIN", "1")
    assert console.plain_requested(False) is False


def test_mb_plain_env_engages_the_kill_switch(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("MB_PLAIN", "1")
    assert console.plain_requested(None) is True
    monkeypatch.setenv("MB_PLAIN", "0")
    assert console.plain_requested(None) is False


def test_ci_and_tty_do_not_engage_the_kill_switch(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert console.plain_requested(None) is False


# --- animations axis: interactive TTY only, never in CI, never forced by color ---


def test_animations_require_an_interactive_tty(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert console.animations_allowed() is True
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert console.animations_allowed() is False


def test_animations_disabled_in_ci_even_on_a_tty(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert console.animations_allowed() is False


def test_force_color_does_not_force_animations(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    console.configure()
    assert console.animations_active is False
    assert console.output.is_terminal, "FORCE_COLOR should still color the console"


# --- color axis: FORCE_COLOR on, NO_COLOR off, --plain kills everything ---


def test_ci_with_force_color_is_colored_but_static(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    console.configure()
    assert console.plain_active is False
    assert console.animations_active is False
    console.success("done")
    assert "\x1b[" in capsys.readouterr().out


def test_force_color_emits_ansi_when_piped(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    console.configure()
    console.success("done")
    assert "\x1b[" in capsys.readouterr().out


def test_no_color_strips_ansi(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("NO_COLOR", "1")
    console.configure()
    console.success("done")
    captured = capsys.readouterr().out
    assert f"{console.GLYPH_PASS} done" in strip_ansi(captured)
    assert "\x1b[3" not in captured, "no color codes may appear under NO_COLOR"


def test_plain_flag_wins_over_force_color(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    console.configure(plain=True)
    console.success("done")
    captured = capsys.readouterr()
    assert f"{console.GLYPH_PASS} done" in captured.out
    assert "\x1b[" not in captured.out


def test_both_modes_print_the_same_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    console.configure(plain=False)
    console.success("gates passed")
    colored_text = strip_ansi(capsys.readouterr().out)
    console.configure(plain=True)
    console.success("gates passed")
    assert capsys.readouterr().out == colored_text


def test_error_and_warn_go_to_stderr(capsys: pytest.CaptureFixture[str]):
    console.configure(plain=True)
    console.error("boom")
    console.warn("careful")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{console.GLYPH_FAIL} boom" in captured.err
    assert f"{console.GLYPH_WARN} careful" in captured.err


def test_raw_does_not_interpret_markup(capsys: pytest.CaptureFixture[str]):
    console.configure(plain=True)
    console.raw("literal [bold]tags[/bold] stay")
    assert "literal [bold]tags[/bold] stay" in capsys.readouterr().out


# --- spinner: animated only on an interactive TTY, static line otherwise ---


def test_spinner_plain_mode_prints_static_line(capsys: pytest.CaptureFixture[str]):
    console.configure(plain=True)
    entered = False
    with console.spinner("Working"):
        entered = True
    captured = capsys.readouterr()
    assert entered
    assert "Working…" in captured.out
    assert "\x1b[" not in captured.out


def test_spinner_static_when_piped_even_with_force_color(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    console.configure()
    with console.spinner("Working"):
        ...
    assert "Working…" in strip_ansi(capsys.readouterr().out)


def test_spinner_animates_on_an_interactive_tty(monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    console.configure()
    seen: list[str] = []

    @contextmanager
    def fake_status(message: str, **_kwargs: object):
        seen.append(message)
        yield

    monkeypatch.setattr(console.output, "status", fake_status)
    with console.spinner("Working"):
        ...
    assert seen == ["Working…"]


# --- the global CLI kill-switch ---


def test_plain_flag_forces_plain_even_when_env_says_rich(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MB_PLAIN", "0")
    result = cli.invoke(app, ["--plain", "lint"])
    assert result.exit_code == 0
    assert console.plain_active is True


def test_no_plain_flag_beats_mb_plain_env(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MB_PLAIN", "1")
    result = cli.invoke(app, ["--no-plain", "lint"])
    assert result.exit_code == 0
    assert console.plain_active is False


def test_mb_plain_env_engages_without_flag(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MB_PLAIN", "1")
    result = cli.invoke(app, ["lint"])
    assert result.exit_code == 0
    assert console.plain_active is True


def test_ci_env_disables_animations_but_not_the_kill_switch(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    clear_mode_env(monkeypatch)
    monkeypatch.setenv("CI", "1")
    result = cli.invoke(app, ["lint"])
    assert result.exit_code == 0
    assert console.plain_active is False
    assert console.animations_active is False


def test_exit_codes_are_identical_across_modes(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    recorder.exit_codes["ruff check"] = 7
    monkeypatch.delenv("MB_PLAIN", raising=False)
    assert cli.invoke(app, ["--plain", "lint"]).exit_code == 7
    assert cli.invoke(app, ["--no-plain", "lint"]).exit_code == 7
