import json

import pytest
from command_recording import CommandRecorder
from mb.main import app
from typer.testing import CliRunner

from mb import release, runner

cli = CliRunner()

TAG_PIPELINE_URL = "https://gitlab.com/meetupbot/mitup-telegram-bot/-/pipelines/42"


@pytest.fixture(autouse=True)
def plain_wide_console(monkeypatch: pytest.MonkeyPatch):
    """Force plain, unwrapped output so assertions can match whole lines."""
    monkeypatch.setenv("MB_PLAIN", "1")
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def no_poll_sleep(monkeypatch: pytest.MonkeyPatch):
    """Never actually sleep between pipeline polls; the tests drive the responses directly."""
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)


def green_repo(recorder: CommandRecorder, *, tags: str = "v1.2.3\n", head: str = "abc123def456") -> CommandRecorder:
    """Prime the recorder with a green origin/main tip at *head*.

    The pre-release green check filters pipelines by `sha`; the post-push watch filters by `ref`, so
    the two queries are keyed separately — a test can override one without disturbing the other.
    """
    recorder.captured_outputs.update(
        {
            "rev-parse origin/main": head,
            "pipelines?sha=": json.dumps([{"id": 7, "status": "success"}]),
            "pipelines?ref=": json.dumps([{"id": 42, "status": "created", "web_url": TAG_PIPELINE_URL}]),
            "tag --list": tags,
        }
    )
    return recorder


def test_release_tags_and_pushes_next_patch(recorder: CommandRecorder):
    green_repo(recorder)

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert ["git", "fetch", "--tags", "origin"] in recorder.commands
    assert ["git", "tag", "-a", "v1.2.4", "abc123def456", "-m", "v1.2.4"] in recorder.commands
    assert ["git", "push", "origin", "v1.2.4"] in recorder.commands
    assert ["glab", "api", "projects/:id/pipelines?ref=v1.2.4"] in recorder.commands
    assert "Released v1.2.4" in result.output
    assert f"Deploy pipeline started: {TAG_PIPELINE_URL}" in result.output


def test_release_minor_bump(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--minor"])

    assert ["git", "tag", "-a", "v1.3.0", "abc123def456", "-m", "v1.3.0"] in recorder.commands


def test_release_major_bump(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--major"])

    assert ["git", "tag", "-a", "v2.0.0", "abc123def456", "-m", "v2.0.0"] in recorder.commands


def test_release_first_ever_tag_is_v0_1_0(recorder: CommandRecorder):
    green_repo(recorder, tags="")

    cli.invoke(app, ["release"])

    assert ["git", "tag", "-a", "v0.1.0", "abc123def456", "-m", "v0.1.0"] in recorder.commands


def test_release_ignores_non_semver_tags(recorder: CommandRecorder):
    green_repo(recorder, tags="v1.0\nvnightly\nv2.4.9\nv2.4.10\n")

    cli.invoke(app, ["release"])

    assert ["git", "tag", "-a", "v2.4.11", "abc123def456", "-m", "v2.4.11"] in recorder.commands


def test_release_rejects_both_bump_flags(recorder: CommandRecorder):
    green_repo(recorder)

    result = cli.invoke(app, ["release", "--minor", "--major"])

    assert result.exit_code != 0
    assert "at most one of --minor/--major" in result.output
    assert not any("tag" in command for command in recorder.commands)


def test_release_tags_origin_main_regardless_of_local_state(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["rev-parse origin/main"] = "999fedcba000"
    # A dirty tree, untracked files, and a feature branch must not influence the release.
    recorder.captured_outputs["status --porcelain"] = " M tools/mb/src/mb/release.py\n?? scratch.txt"
    recorder.captured_outputs["rev-parse --abbrev-ref HEAD"] = "feature-branch"

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert ["glab", "api", "projects/:id/pipelines?sha=999fedcba000"] in recorder.commands
    assert ["git", "tag", "-a", "v1.2.4", "999fedcba000", "-m", "v1.2.4"] in recorder.commands
    assert ["git", "status", "--porcelain"] not in recorder.commands


def test_release_aborts_when_no_pipeline_exists(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["pipelines?sha="] = "[]"

    result = cli.invoke(app, ["release"])

    assert result.exit_code != 0
    assert "No pipeline found" in result.output


def test_release_aborts_when_latest_pipeline_is_not_green(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["pipelines?sha="] = json.dumps(
        [{"id": 5, "status": "success"}, {"id": 9, "status": "running"}]
    )

    result = cli.invoke(app, ["release"])

    assert result.exit_code != 0
    assert "not 'success'" in result.output


def test_release_uses_latest_pipeline_even_after_an_older_failure(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["pipelines?sha="] = json.dumps(
        [{"id": 3, "status": "failed"}, {"id": 8, "status": "success"}]
    )

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0


def test_release_falls_back_to_pipelines_listing_when_no_pipeline_starts(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["pipelines?ref="] = "[]"

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "Released v1.2.4" in result.output
    assert "has not appeared yet" in result.output
    assert f"{release.PIPELINES_URL}?ref=v1.2.4" in result.output


def test_pipeline_url_for_ref_waits_until_a_pipeline_appears(monkeypatch: pytest.MonkeyPatch):
    responses = iter(["[]", json.dumps([{"id": 5, "status": "created", "web_url": "https://x/5"}])])
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (0, next(responses)))
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.pipeline_url_for_ref("v1.2.4") == "https://x/5"


def test_pipeline_url_for_ref_gives_up_after_the_poll_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (0, "[]"))
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.pipeline_url_for_ref("v1.2.4") is None


@pytest.mark.parametrize(
    ("exit_codes", "outputs", "expected_message"),
    [
        pytest.param({"rev-parse origin/main": 1}, {}, "`git rev-parse origin/main` failed", id="git-read-fails"),
        pytest.param({"fetch": 1}, {}, None, id="fetch-fails"),
        pytest.param(
            {"pipelines?sha=": 1}, {}, "Could not query the GitLab pipeline status", id="pipeline-query-fails"
        ),
        pytest.param(
            {},
            {"pipelines?sha=": "not-json"},
            "Could not parse the GitLab pipeline response",
            id="unparseable-response",
        ),
        pytest.param({"tag -a": 1}, {}, None, id="tag-creation-fails"),
        pytest.param({"push origin": 1}, {}, None, id="tag-push-fails"),
    ],
)
def test_release_aborts_on_command_failure(
    recorder: CommandRecorder,
    exit_codes: dict[str, int],
    outputs: dict[str, str],
    expected_message: str | None,
):
    green_repo(recorder)
    recorder.exit_codes.update(exit_codes)
    recorder.captured_outputs.update(outputs)

    result = cli.invoke(app, ["release"])

    assert result.exit_code != 0
    assert "Released" not in result.output
    if expected_message is not None:
        assert expected_message in result.output


@pytest.mark.parametrize(
    ("current", "bump", "expected"),
    [
        ((1, 4, 2), "patch", (1, 4, 3)),
        ((1, 4, 2), "minor", (1, 5, 0)),
        ((1, 4, 2), "major", (2, 0, 0)),
        (None, "patch", (0, 1, 0)),
        (None, "major", (0, 1, 0)),
    ],
)
def test_next_version(current: release.Version | None, bump: str, expected: release.Version):
    assert release.next_version(current, bump) == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v1.2.3", (1, 2, 3)), ("v0.0.0", (0, 0, 0)), ("v1.2", None), ("v1.2.3-rc1", None), ("nightly", None)],
)
def test_parse_version(tag: str, expected: release.Version | None):
    assert release.parse_version(tag) == expected


def test_format_version():
    assert release.format_version((2, 0, 5)) == "v2.0.5"
