import json
import subprocess

import pytest

from mb import console, tokens_ops

SERVICE_ACCOUNTS = [
    {"id": 1, "username": "mitup-gitlab-bot"},
    {"id": 2, "username": "mitup-triage-bot"},
]
# " mr-api" carries the leading space its real counterpart was saved with; matching is
# on stripped names.
BOT_TOKENS = [
    {"id": 11, "name": " mr-api", "active": True, "expires_at": "2027-08-23"},
    {"id": 12, "name": "mr-push", "active": True, "expires_at": "2027-08-23"},
    {"id": 13, "name": "mitup-gitlab-bot-ci", "active": False, "expires_at": "2027-07-13"},
]
TRIAGE_TOKENS = [{"id": 21, "name": "triage-api", "active": True, "expires_at": "2027-08-23"}]
GROUP_TOKENS: list[dict[str, object]] = [
    {"id": 31, "name": "Renovate 2026", "active": True, "expires_at": "2027-05-15"},
    {"id": 32, "name": "Renovate 2026", "active": False, "expires_at": "2026-05-15"},
]

ACCOUNTS_PATH = "groups/meetupbot/service_accounts"
GROUP_TOKENS_PATH = "groups/meetupbot/access_tokens"
VARIABLE_PATHS = tuple(entry.variable.api_path for entry in tokens_ops.ROTATIONS)
API_VARIABLE_PATH, PUSH_VARIABLE_PATH, TRIAGE_VARIABLE_PATH, RENOVATE_VARIABLE_PATH = VARIABLE_PATHS


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch):
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


class FakeGlab:
    """Serves the endpoints the rotation touches, recording every `glab api` argv.

    Variables live in a dict so a PUT followed by a GET behaves like the real API;
    rotate calls mint deterministic ``glpat-rotated-<token id>`` values. ``fail_on``
    makes the call whose path contains it fail; ``drop_writes`` turns PUTs into no-ops
    so the read-back verification fails.
    """

    def __init__(
        self,
        fail_on: str | None = None,
        drop_writes: bool = False,
        group_tokens: list[dict[str, object]] | None = None,
    ):
        self.fail_on = fail_on
        self.drop_writes = drop_writes
        self.group_tokens = GROUP_TOKENS if group_tokens is None else group_tokens
        self.calls: list[list[str]] = []
        self.variables = {path: f"old-{index}" for index, path in enumerate(VARIABLE_PATHS)}

    def __call__(self, argv: list[str], **kwargs: object) -> tuple[int, str]:
        assert argv[:2] == ["glab", "api"]
        args = argv[2:]
        self.calls.append(args)
        path = args[0]
        if self.fail_on is not None and self.fail_on in path:
            return 1, "simulated glab failure"
        return 0, self.respond(path, args)

    def respond(self, path: str, args: list[str]) -> str:
        if path == ACCOUNTS_PATH:
            return json.dumps(SERVICE_ACCOUNTS)
        if path == f"{ACCOUNTS_PATH}/1/personal_access_tokens":
            return json.dumps(BOT_TOKENS)
        if path == f"{ACCOUNTS_PATH}/2/personal_access_tokens":
            return json.dumps(TRIAGE_TOKENS)
        if path == GROUP_TOKENS_PATH:
            return json.dumps(self.group_tokens)
        if path.endswith("/rotate"):
            return json.dumps({"token": f"glpat-rotated-{path.split('/')[-2]}"})
        if path in self.variables:
            return self.respond_variable(path, args)
        raise AssertionError(f"Unexpected glab path {path!r}")

    def respond_variable(self, path: str, args: list[str]) -> str:
        if "PUT" not in args:
            return json.dumps({"value": self.variables[path]})
        if not self.drop_writes:
            value_field = args[args.index("-f") + 1]
            self.variables[path] = value_field.removeprefix("value=")
        return json.dumps({"value": self.variables[path]})


class ClipboardRecorder:
    """Records every pbcopy payload without touching the real clipboard."""

    def __init__(self):
        self.copied: list[bytes] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert argv == ["pbcopy"]
        payload = kwargs.get("input")
        assert isinstance(payload, bytes)
        self.copied.append(payload)
        return subprocess.CompletedProcess(argv, 0)


def install_glab(monkeypatch: pytest.MonkeyPatch, fake: FakeGlab):
    monkeypatch.setattr(tokens_ops.runner, "run_quiet", fake)


def install_prompts(monkeypatch: pytest.MonkeyPatch, confirm: bool = True):
    monkeypatch.setattr(tokens_ops.typer, "confirm", lambda *args, **kwargs: confirm)
    monkeypatch.setattr(tokens_ops.typer, "prompt", lambda *args, **kwargs: "")


@pytest.fixture
def clipboard(monkeypatch: pytest.MonkeyPatch) -> ClipboardRecorder:
    recorder = ClipboardRecorder()
    monkeypatch.setattr(tokens_ops.shutil, "which", lambda name: "/usr/bin/pbcopy")
    monkeypatch.setattr(tokens_ops.subprocess, "run", recorder)
    return recorder


def rotate_calls(fake: FakeGlab) -> list[list[str]]:
    return [call for call in fake.calls if call[0].endswith("/rotate")]


def test_dry_run_prints_the_plan_and_rotates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    fake = FakeGlab()
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    assert tokens_ops.rotate_tokens(dry_run=True) == 0

    output = combined(capsys)
    assert "mr-api (mitup-gitlab-bot)" in output
    assert "MITUP_GITLAB_API_TOKEN" in output
    assert "Renovate 2026 (group meetupbot)" in output
    assert "Dry run" in output
    assert rotate_calls(fake) == []
    assert fake.variables[API_VARIABLE_PATH] == "old-0"


def test_rotation_updates_every_variable_and_hands_off_values(
    monkeypatch: pytest.MonkeyPatch, clipboard: ClipboardRecorder
):
    fake = FakeGlab()
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    assert tokens_ops.rotate_tokens(dry_run=False) == 0

    assert fake.variables == {
        API_VARIABLE_PATH: "glpat-rotated-11",
        PUSH_VARIABLE_PATH: "glpat-rotated-12",
        TRIAGE_VARIABLE_PATH: "glpat-rotated-21",
        RENOVATE_VARIABLE_PATH: "glpat-rotated-31",
    }
    expires_field = f"expires_at={tokens_ops.next_expiry()}"
    assert all(expires_field in call for call in rotate_calls(fake))
    assert len(rotate_calls(fake)) == 4
    # Each value is copied for the password manager; the trailing empty payload is the
    # final clipboard clear.
    assert clipboard.copied == [
        b"glpat-rotated-11",
        b"glpat-rotated-12",
        b"glpat-rotated-21",
        b"glpat-rotated-31",
        b"",
    ]


def test_token_values_never_reach_the_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    fake = FakeGlab()
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    tokens_ops.rotate_tokens(dry_run=False)

    assert "glpat-rotated" not in combined(capsys)


def test_missing_variable_fails_the_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    fake = FakeGlab(fail_on="MITUP_GITLAB_PUSH_TOKEN")
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    assert tokens_ops.rotate_tokens(dry_run=False) == 1

    assert "MITUP_GITLAB_PUSH_TOKEN" in combined(capsys)
    assert rotate_calls(fake) == []


def test_ambiguous_token_name_fails_the_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    duplicated: list[dict[str, object]] = [
        *GROUP_TOKENS,
        {"id": 33, "name": "Renovate 2026", "active": True, "expires_at": "2027-06-01"},
    ]
    fake = FakeGlab(group_tokens=duplicated)
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    assert tokens_ops.rotate_tokens(dry_run=False) == 1

    assert "Renovate 2026" in combined(capsys)
    assert rotate_calls(fake) == []


def test_declined_confirmation_rotates_nothing(monkeypatch: pytest.MonkeyPatch, clipboard: ClipboardRecorder):
    fake = FakeGlab()
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch, confirm=False)

    assert tokens_ops.rotate_tokens(dry_run=False) == 1

    assert rotate_calls(fake) == []


def test_failed_verification_stops_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    fake = FakeGlab(drop_writes=True)
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)

    assert tokens_ops.rotate_tokens(dry_run=False) == 1

    output = combined(capsys)
    assert "MITUP_GITLAB_API_TOKEN" in output
    assert "already revoked" in output
    assert len(rotate_calls(fake)) == 1


def test_missing_pbcopy_warns_but_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clipboard: ClipboardRecorder
):
    fake = FakeGlab()
    install_glab(monkeypatch, fake)
    install_prompts(monkeypatch)
    monkeypatch.setattr(tokens_ops.shutil, "which", lambda name: None)

    assert tokens_ops.rotate_tokens(dry_run=False) == 0

    assert "pbcopy is unavailable" in combined(capsys)
    assert clipboard.copied == []
