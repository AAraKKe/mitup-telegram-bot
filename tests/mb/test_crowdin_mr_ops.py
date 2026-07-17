import subprocess
from collections.abc import Callable, Iterable

import httpx
import pytest

from mb import console, crowdin_mr_ops, runner

API_V4_URL = "https://gitlab.example/api/v4"
PROJECT_ID = "724481"
SERVER_HOST = "gitlab.example"
PROJECT_PATH = "meetupbot/mitup-telegram-bot"
DEFAULT_BRANCH = "main"
TOKEN = "glpat-SECRETVALUE"

# The oauth2:<token>@ remote the push targets; the token must never reach console output.
REMOTE_URL = f"https://oauth2:{TOKEN}@{SERVER_HOST}/{PROJECT_PATH}.git"

FULL_ENV = {
    "RENOVATE_TOKEN": TOKEN,
    "CI_API_V4_URL": API_V4_URL,
    "CI_PROJECT_ID": PROJECT_ID,
    "CI_SERVER_HOST": SERVER_HOST,
    "CI_PROJECT_PATH": PROJECT_PATH,
    "CI_DEFAULT_BRANCH": DEFAULT_BRANCH,
}


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch):
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


@pytest.fixture(autouse=True)
def stub_repo_root(monkeypatch: pytest.MonkeyPatch):
    # Keep git subprocesses from ever shelling out for the repo root during a test.
    monkeypatch.setattr(runner, "repo_root", lambda: "/repo")


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def set_ci_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None):
    for name, value in {**FULL_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


class GitRecorder:
    """Records the git argv of every subprocess.run without executing anything.

    ``diff_returncode`` answers the ``git diff --quiet`` freshness probe (nonzero means
    the catalogs changed); ``fail_on`` names a git subcommand that raises
    CalledProcessError to simulate a push/commit failure.
    """

    def __init__(self, diff_returncode: int = 1, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.diff_returncode = diff_returncode
        self.fail_on = fail_on

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(args))
        if args[1] == "diff":
            return subprocess.CompletedProcess(args, self.diff_returncode)
        if self.fail_on is not None and self.fail_on in args:
            raise subprocess.CalledProcessError(returncode=128, cmd=list(args))
        return subprocess.CompletedProcess(args, 0)


def install_git_recorder(monkeypatch: pytest.MonkeyPatch, recorder: GitRecorder):
    monkeypatch.setattr(crowdin_mr_ops.subprocess, "run", recorder)


def install_hold_response(monkeypatch: pytest.MonkeyPatch, payload: object) -> dict[str, object]:
    """Patch httpx.get to serve ``payload`` and record the outbound request kwargs."""
    seen: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        seen["url"] = url
        seen.update(kwargs)
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(crowdin_mr_ops.httpx, "get", fake_get)
    return seen


# --- ci_environment ---


def test_ci_environment_returns_all_values(monkeypatch: pytest.MonkeyPatch):
    set_ci_env(monkeypatch)

    assert crowdin_mr_ops.ci_environment() == FULL_ENV


@pytest.mark.parametrize(
    "missing",
    ["RENOVATE_TOKEN", "CI_API_V4_URL", "CI_PROJECT_ID", "CI_SERVER_HOST", "CI_PROJECT_PATH", "CI_DEFAULT_BRANCH"],
)
def test_ci_environment_reports_missing_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], missing: str
):
    set_ci_env(monkeypatch, **{missing: None})

    assert crowdin_mr_ops.ci_environment() is None
    output = combined(capsys)
    assert missing in output
    assert "GitLab CI" in output


def test_ci_environment_lists_every_missing_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    set_ci_env(monkeypatch, CI_PROJECT_ID=None, CI_DEFAULT_BRANCH=None)

    assert crowdin_mr_ops.ci_environment() is None
    output = combined(capsys)
    assert "CI_PROJECT_ID" in output
    assert "CI_DEFAULT_BRANCH" in output


# --- hold_requested ---


def test_hold_requested_true_when_label_present(monkeypatch: pytest.MonkeyPatch):
    seen = install_hold_response(monkeypatch, [{"labels": ["something", "crowdin::hold"]}])

    assert crowdin_mr_ops.hold_requested(FULL_ENV) is True
    assert seen["url"] == f"{API_V4_URL}/projects/{PROJECT_ID}/merge_requests"
    assert seen["params"] == {"state": "opened", "source_branch": "crowdin-translations"}
    assert seen["headers"] == {"PRIVATE-TOKEN": TOKEN}


def test_hold_requested_false_when_label_absent(monkeypatch: pytest.MonkeyPatch):
    install_hold_response(monkeypatch, [{"labels": ["other"]}, {"labels": []}])

    assert crowdin_mr_ops.hold_requested(FULL_ENV) is False


def test_hold_requested_false_when_no_open_mr(monkeypatch: pytest.MonkeyPatch):
    install_hold_response(monkeypatch, [])

    assert crowdin_mr_ops.hold_requested(FULL_ENV) is False


def test_hold_requested_raises_on_error_status(monkeypatch: pytest.MonkeyPatch):
    request = httpx.Request("GET", f"{API_V4_URL}/projects/{PROJECT_ID}/merge_requests")
    monkeypatch.setattr(crowdin_mr_ops.httpx, "get", lambda *a, **k: httpx.Response(500, request=request))

    with pytest.raises(httpx.HTTPStatusError):
        crowdin_mr_ops.hold_requested(FULL_ENV)


# --- catalogs_changed ---


@pytest.mark.parametrize(
    "diff_returncode, changed",
    [(1, True), (0, False)],
    ids=["diff_reports_changes", "diff_reports_clean"],
)
def test_catalogs_changed_reads_diff_exit_code(monkeypatch: pytest.MonkeyPatch, diff_returncode: int, changed: bool):
    recorder = GitRecorder(diff_returncode=diff_returncode)
    install_git_recorder(monkeypatch, recorder)

    assert crowdin_mr_ops.catalogs_changed() is changed
    assert recorder.calls == [["git", "diff", "--quiet", "--", "libs/core/mitup_bot/locales/*.po"]]


# --- push_translations_branch ---


def test_push_translations_branch_runs_full_git_sequence(monkeypatch: pytest.MonkeyPatch):
    recorder = GitRecorder()
    install_git_recorder(monkeypatch, recorder)

    crowdin_mr_ops.push_translations_branch(FULL_ENV)

    assert recorder.calls == [
        ["git", "config", "user.name", "Crowdin Sync"],
        ["git", "config", "user.email", "crowdin-sync-bot@mitup.social"],
        ["git", "checkout", "-B", "crowdin-translations"],
        ["git", "add", "--", "libs/core/mitup_bot/locales/*.po"],
        ["git", "commit", "-m", "🗣️ Update approved translations from Crowdin"],
        [
            "git",
            "push",
            "--force",
            "-o",
            "merge_request.create",
            "-o",
            "merge_request.target=main",
            "-o",
            "merge_request.title=🗣️ Update approved translations from Crowdin",
            REMOTE_URL,
            "HEAD:crowdin-translations",
        ],
    ]


# --- create_translations_mr ---


def drive_create_mr(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hold_payload: Iterable[object] = (),
    diff_returncode: int = 1,
    fail_on: str | None = None,
    get_override: Callable[..., httpx.Response] | None = None,
) -> GitRecorder:
    set_ci_env(monkeypatch)
    if get_override is not None:
        monkeypatch.setattr(crowdin_mr_ops.httpx, "get", get_override)
    else:
        install_hold_response(monkeypatch, list(hold_payload))
    recorder = GitRecorder(diff_returncode=diff_returncode, fail_on=fail_on)
    install_git_recorder(monkeypatch, recorder)
    return recorder


def test_create_translations_mr_happy_path_pushes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    recorder = drive_create_mr(monkeypatch, hold_payload=[{"labels": ["ready"]}], diff_returncode=1)

    assert crowdin_mr_ops.create_translations_mr() == 0
    pushed = [call for call in recorder.calls if call[1] == "push"]
    assert len(pushed) == 1  # the branch was force-pushed exactly once
    assert "Force-pushed crowdin-translations" in combined(capsys)


def test_create_translations_mr_missing_env_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    set_ci_env(monkeypatch, RENOVATE_TOKEN=None)

    assert crowdin_mr_ops.create_translations_mr() == 1
    assert "RENOVATE_TOKEN" in combined(capsys)


def test_create_translations_mr_hold_leaves_branch_untouched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    recorder = drive_create_mr(monkeypatch, hold_payload=[{"labels": ["crowdin::hold"]}])

    assert crowdin_mr_ops.create_translations_mr() == 0
    assert recorder.calls == []  # no diff probe and no git writes past the hold check
    assert "crowdin::hold" in combined(capsys)


def test_create_translations_mr_no_changes_skips_push(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    recorder = drive_create_mr(monkeypatch, hold_payload=[], diff_returncode=0)

    assert crowdin_mr_ops.create_translations_mr() == 0
    assert [call for call in recorder.calls if call[1] == "push"] == []
    assert "Catalogs already match" in combined(capsys)


def test_create_translations_mr_http_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def raise_connect(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    drive_create_mr(monkeypatch, get_override=raise_connect)

    assert crowdin_mr_ops.create_translations_mr() == 1
    assert "GitLab API error" in combined(capsys)


def test_create_translations_mr_git_failure_hides_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    drive_create_mr(monkeypatch, hold_payload=[], diff_returncode=1, fail_on="push")

    assert crowdin_mr_ops.create_translations_mr() == 1
    output = combined(capsys)
    assert "exit code 128" in output
    assert TOKEN not in output  # the token rides in the push remote URL and must never leak
