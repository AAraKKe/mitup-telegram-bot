from collections import Counter

import pytest
from command_recording import CommandRecorder
from mb import console, locales, testing
from mb.main import app
from typer.testing import CliRunner

CLI_RUNNER = CliRunner()

# The exact pytest arguments the .gitlab/ci/test.yml jobs ran before they were flipped to
# `mb test`. The tests below prove the flipped invocations compose a flag-for-flag equivalent
# argv, so the CI change is behaviour-preserving.
LEGACY_TEST_JOB_PYTEST_ARGS = [
    "-n",
    "4",
    "--cov=mitup_bot",
    "--cov-report",
    "term-missing:skip-covered",
    "--cov-report",
    "xml:coverage.xml",
    "--junitxml=report.xml",
    "--json-report",
    "--json-report-file=report.json",
    "tests",
    "--lang",
    "en",
]
LEGACY_TEST_DB_JOB_PYTEST_ARGS = [
    "--db-tests",
    "--dist",
    "no",
    "--junitxml=report.xml",
    "tests/models/db_behavior/",
]


def test_default_is_fast_mode():
    command = testing.build_pytest_command([])

    assert command == ["uv", "run", "pytest", "-n", "4", "--no-cov", "--tb=short", "-q", "tests"]


def test_user_args_replace_the_default_target():
    command = testing.build_pytest_command(["tests/utils", "tests/views"])

    assert command[-2:] == ["tests/utils", "tests/views"]
    assert "tests" not in command[:-2]


def test_cov_mode_adds_coverage_flags():
    command = testing.build_pytest_command([], cov=True)

    assert command == [
        "uv",
        "run",
        "pytest",
        "-n",
        "4",
        "--cov=mitup_bot",
        "--cov-report",
        "term-missing:skip-covered",
        "tests",
    ]


def test_db_mode_targets_the_db_suite():
    command = testing.build_pytest_command([], db=True)

    assert command == ["uv", "run", "pytest", "--db-tests", "--dist", "no", "tests/models/db_behavior/"]


def test_lang_and_pytest_flags_are_forwarded():
    command = testing.build_pytest_command(["tests/utils", "-k", "menu"], lang="es_ES")

    assert command == [
        "uv",
        "run",
        "pytest",
        "-n",
        "4",
        "--no-cov",
        "--tb=short",
        "-q",
        "--lang",
        "es_ES",
        "tests/utils",
        "-k",
        "menu",
    ]


def test_cov_and_db_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        testing.build_pytest_command([], cov=True, db=True)


def test_run_tests_skips_locale_build_when_fresh(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    exit_code = testing.run_tests([])

    assert exit_code == 0
    assert len(recorder.commands) == 1
    assert recorder.commands[0][:3] == ["uv", "run", "pytest"]


def test_run_tests_builds_locales_when_stale(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: True)

    testing.run_tests([])

    assert recorder.commands[0] == ["uv", "run", "mitup", "translations", "build"]
    assert recorder.commands[1][:3] == ["uv", "run", "pytest"]


def test_run_tests_propagates_locale_build_failure(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: True)
    recorder.exit_codes["translations build"] = 3

    exit_code = testing.run_tests([])

    assert exit_code == 3
    assert len(recorder.commands) == 1, "pytest must not run when the locale build fails"


def test_cov_run_forces_color(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    testing.run_tests([], cov=True)

    assert recorder.calls[0].extra_env == {"FORCE_COLOR": "1"}


def test_fast_run_does_not_force_color(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    testing.run_tests([])

    assert recorder.calls[0].extra_env is None


def test_ci_test_job_composes_equivalent_argv(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """The flipped `test` job's `mb test` invocation must build the legacy pytest argv."""
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(
        app,
        [
            "test",
            "--cov",
            "--lang",
            "en",
            "tests",
            "--cov-report",
            "xml:coverage.xml",
            "--junitxml=report.xml",
            "--json-report",
            "--json-report-file=report.json",
        ],
    )

    assert result.exit_code == 0
    composed = recorder.commands[0]
    assert composed[:3] == ["uv", "run", "pytest"]
    pytest_args = composed[3:]
    assert pytest_args == [
        "-n",
        "4",
        "--cov=mitup_bot",
        "--cov-report",
        "term-missing:skip-covered",
        "--lang",
        "en",
        "tests",
        "--cov-report",
        "xml:coverage.xml",
        "--junitxml=report.xml",
        "--json-report",
        "--json-report-file=report.json",
    ]
    # Order differs from the legacy line only where pytest treats it as insignificant
    # (positional target, accumulated --cov-report, --lang position).
    assert Counter(pytest_args) == Counter(LEGACY_TEST_JOB_PYTEST_ARGS)


def test_ci_test_db_job_composes_equivalent_argv(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """The flipped `test-db` job's `mb test --db` invocation must build the legacy pytest argv."""
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(
        app,
        ["test", "--db", "tests/models/db_behavior/", "--junitxml=report.xml"],
    )

    assert result.exit_code == 0
    composed = recorder.commands[0]
    assert composed[:3] == ["uv", "run", "pytest"]
    pytest_args = composed[3:]
    assert pytest_args == [
        "--db-tests",
        "--dist",
        "no",
        "tests/models/db_behavior/",
        "--junitxml=report.xml",
    ]
    assert Counter(pytest_args) == Counter(LEGACY_TEST_DB_JOB_PYTEST_ARGS)


def test_cov_run_in_plain_mode_does_not_force_subprocess_color(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)
    console.configure(plain=True)

    testing.run_tests([], cov=True)

    assert recorder.calls[0].extra_env is None
