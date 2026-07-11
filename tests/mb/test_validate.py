import pytest
from mb.main import app
from typer.testing import CliRunner

from mb import validate

cli = CliRunner()


def test_run_gates_runs_every_gate_even_after_a_failure():
    executed: list[str] = []

    def gate(name: str, exit_code: int) -> validate.Gate:
        def run() -> int:
            executed.append(name)
            return exit_code

        return (name, run)

    results = validate.run_gates([gate("lint", 1), gate("typecheck", 0), gate("tests", 2)])

    assert executed == ["lint", "typecheck", "tests"]
    assert [result.exit_code for result in results] == [1, 0, 2]


def test_overall_exit_code_is_zero_only_when_all_gates_pass():
    passing = validate.GateResult("lint", 0)
    failing = validate.GateResult("tests", 5)

    assert validate.overall_exit_code([passing, passing]) == 0
    assert validate.overall_exit_code([passing, failing]) == 1


def test_validate_command_fails_but_still_runs_all_gates(monkeypatch: pytest.MonkeyPatch):
    executed: list[str] = []

    def fake_gates() -> list[validate.Gate]:
        return [
            ("format", lambda: executed.append("format") or 0),
            ("lint", lambda: executed.append("lint") or 1),
            ("tests", lambda: executed.append("tests") or 0),
        ]

    monkeypatch.setattr(validate, "standard_gates", fake_gates)

    result = cli.invoke(app, ["validate"])

    assert result.exit_code == 1
    assert executed == ["format", "lint", "tests"]
    assert "FAIL" in result.output
    assert "PASS" in result.output


def test_validate_command_passes_when_all_gates_pass(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(validate, "standard_gates", lambda: [("lint", lambda: 0)])

    result = cli.invoke(app, ["validate"])

    assert result.exit_code == 0


def test_validate_all_appends_extended_gates(monkeypatch: pytest.MonkeyPatch):
    executed: list[str] = []
    monkeypatch.setattr(validate, "standard_gates", lambda: [("lint", lambda: executed.append("lint") or 0)])
    monkeypatch.setattr(validate, "extended_gates", lambda: [("db tests", lambda: executed.append("db tests") or 0)])

    result = cli.invoke(app, ["validate", "--all"])

    assert result.exit_code == 0
    assert executed == ["lint", "db tests"]
