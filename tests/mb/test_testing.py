from collections import Counter
from unittest import mock

import pytest
from command_recording import CommandRecorder
from mb.main import app
from typer.testing import CliRunner

from mb import console, locales, testing

CLI_RUNNER = CliRunner()

# The exact pytest arguments a full-suite coverage run expands to. The tests below pin `mb test`'s
# argv composition so a whole-suite `--cov` invocation stays flag-for-flag stable.
FULL_SUITE_COV_PYTEST_ARGS = [
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
DB_SUITE_PYTEST_ARGS = [
    "--db-tests",
    "--dist",
    "no",
    "--junitxml=report.xml",
    "tests/data/db_behavior/",
]


def test_default_is_fast_mode():
    command = testing.build_pytest_command([], [])

    assert command == ["uv", "run", "pytest", "-n", "4", "--no-cov", "--tb=short", "-q", "tests"]


def test_user_args_replace_the_default_target():
    command = testing.build_pytest_command(["tests/utils", "tests/views"], [])

    assert command[-2:] == ["tests/utils", "tests/views"]
    assert "tests" not in command[:-2]


def test_cov_mode_adds_coverage_flags():
    command = testing.build_pytest_command([], [], cov=True)

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
    command = testing.build_pytest_command([], [], db=True)

    assert command == ["uv", "run", "pytest", "--db-tests", "--dist", "no", "tests/data/db_behavior/"]


def test_member_derives_its_test_subtree():
    command = testing.build_pytest_command([], [], members=["libs/telegram"])

    assert command[-1:] == ["tests/telegram"]


def test_mb_test_path_derives_like_every_member():
    command = testing.build_pytest_command([], [], members=["tools/mb"])

    assert command[-1] == "tests/mb"


def test_mb_cov_target_is_the_derivation_exception():
    command = testing.build_pytest_command([], [], cov=True, members=["tools/mb"])

    assert command[-1] == "tests/mb"
    assert "--cov=tools/mb/src/mb" in command


def test_member_cov_measures_the_whole_namespace():
    command = testing.build_pytest_command([], [], cov=True, members=["libs/data"])

    assert command == [
        "uv",
        "run",
        "pytest",
        "-n",
        "4",
        "--cov=mitup_bot",
        "--cov-report",
        "term-missing:skip-covered",
        "tests/data",
    ]


def test_i18n_selects_the_language_rendering_members():
    command = testing.build_pytest_command([], [], cov=True, i18n=True)

    cov_targets = [arg for arg in command if arg.startswith("--cov=")]
    assert cov_targets == ["--cov=mitup_bot"]
    assert command[-3:] == ["tests/bot", "tests/telegram", "tests/events"]


def test_member_paths_are_overridden_by_explicit_user_args():
    command = testing.build_pytest_command(["tests/views"], [], members=["libs/data"])

    assert command[-1:] == ["tests/views"]


def test_lang_and_pytest_flags_are_forwarded():
    command = testing.build_pytest_command(["tests/utils"], ["-k", "menu"], lang="es_ES")

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


def test_db_with_cov_measures_the_db_suite_serially():
    command = testing.build_pytest_command([], [], cov=True, db=True)

    assert command == [
        "uv",
        "run",
        "pytest",
        "--db-tests",
        "--dist",
        "no",
        "--cov=mitup_bot",
        "--cov-report",
        "term-missing:skip-covered",
        "tests/data/db_behavior/",
    ]


def test_report_flags_stay_off_the_positional_target():
    command = testing.build_pytest_command(
        [], testing.report_flags("not i18n", "report.xml", "coverage.xml"), cov=True, members=["apps/bot"]
    )

    assert command[-6:] == [
        "tests/bot",
        "-m",
        "not i18n",
        "--junitxml=report.xml",
        "--cov-report",
        "xml:coverage.xml",
    ]


def test_member_job_cli_keeps_the_member_target(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """Reporter options must not displace the resolved member target."""
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(
        app,
        ["test", "--member", "libs/monitoring", "--cov", "--cov-xml", "coverage.xml", "--junit", "report.xml"],
    )

    assert result.exit_code == 0
    pytest_args = recorder.commands[0][3:]
    assert "tests/monitoring" in pytest_args
    assert "--cov=mitup_bot" in pytest_args
    assert pytest_args[-3:] == ["--junitxml=report.xml", "--cov-report", "xml:coverage.xml"]


def test_i18n_matrix_cli_forwards_marker_and_language(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(
        app,
        ["test", "--member", "apps/bot", "--lang", "de_DE", "--cov", "-m", "i18n"],
    )

    assert result.exit_code == 0
    pytest_args = recorder.commands[0][3:]
    assert pytest_args[pytest_args.index("--lang") + 1] == "de_DE"
    assert pytest_args[-2:] == ["-m", "i18n"]
    assert "tests/bot" in pytest_args


def test_unknown_member_derives_a_missing_path(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """There is no validation table to reject an unknown member; it derives a path that does not
    exist, so pytest fails at collection rather than the run silently passing."""
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(app, ["test", "--member", "libs/nope"])

    assert result.exit_code == 0  # the recorder stubs the subprocess; a real run fails on the path
    assert recorder.commands[0][-1] == "tests/nope"


def test_run_tests_skips_locale_build_when_fresh(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    exit_code = testing.run_tests([], [])

    assert exit_code == 0
    assert len(recorder.commands) == 1
    assert recorder.commands[0][:3] == ["uv", "run", "pytest"]


def test_run_tests_builds_locales_when_stale(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: True)
    build = mock.Mock(return_value=0)
    monkeypatch.setattr(locales, "build_locales", build)

    testing.run_tests([], [])

    build.assert_called_once()
    assert recorder.commands[0][:3] == ["uv", "run", "pytest"]


def test_run_tests_propagates_locale_build_failure(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: True)
    monkeypatch.setattr(locales, "build_locales", lambda: 3)

    exit_code = testing.run_tests([], [])

    assert exit_code == 3
    assert recorder.commands == [], "pytest must not run when the locale build fails"


def test_cov_run_forces_color(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    testing.run_tests([], [], cov=True)

    assert recorder.calls[0].extra_env == {"FORCE_COLOR": "1"}


def test_fast_run_does_not_force_color(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    testing.run_tests([], [])

    assert recorder.calls[0].extra_env is None


def test_ci_test_job_composes_equivalent_argv(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """A whole-suite `mb test --cov` invocation must build the expected pytest argv."""
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
    # Order differs from the reference args only where pytest treats it as insignificant
    # (positional target, accumulated --cov-report, --lang position).
    assert Counter(pytest_args) == Counter(FULL_SUITE_COV_PYTEST_ARGS)


def test_ci_test_db_job_composes_equivalent_argv(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    """The `test-db` job's `mb test --db` invocation must build the expected pytest argv."""
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)

    result = CLI_RUNNER.invoke(
        app,
        ["test", "--db", "tests/data/db_behavior/", "--junitxml=report.xml"],
    )

    assert result.exit_code == 0
    composed = recorder.commands[0]
    assert composed[:3] == ["uv", "run", "pytest"]
    pytest_args = composed[3:]
    assert pytest_args == [
        "--db-tests",
        "--dist",
        "no",
        "tests/data/db_behavior/",
        "--junitxml=report.xml",
    ]
    assert Counter(pytest_args) == Counter(DB_SUITE_PYTEST_ARGS)


def test_cov_run_in_plain_mode_does_not_force_subprocess_color(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(locales, "locales_stale", lambda locales_dir: False)
    console.configure(plain=True)

    testing.run_tests([], [], cov=True)

    assert recorder.calls[0].extra_env is None
