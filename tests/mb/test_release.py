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
    *tags* are served as origin's `ls-remote` listing, each with its peeled `^{}` companion line.
    """
    listing = "".join(
        f"cafe{index:04x}\trefs/tags/{tag}\ncafe{index:04x}\trefs/tags/{tag}^{{}}\n"
        for index, tag in enumerate(tags.split())
    )
    recorder.captured_outputs.update(
        {
            "rev-parse origin/main": head,
            "pipelines?sha=": json.dumps([{"id": 7, "status": "success"}]),
            "pipelines?ref=": json.dumps([{"id": 42, "status": "created", "web_url": TAG_PIPELINE_URL}]),
            "ls-remote --tags": listing,
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


def test_release_patch_bump(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--patch"])

    assert ["git", "tag", "-a", "v1.2.4", "abc123def456", "-m", "v1.2.4"] in recorder.commands


def test_release_minor_bump(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--minor"])

    assert ["git", "tag", "-a", "v1.3.0", "abc123def456", "-m", "v1.3.0"] in recorder.commands


def test_release_major_bump(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--major"])

    assert ["git", "tag", "-a", "v2.0.0", "abc123def456", "-m", "v2.0.0"] in recorder.commands


RELEASE_POST_PREFIX = ["glab", "api", "-X", "POST", "projects/:id/releases"]


def release_create_call(recorder: CommandRecorder) -> list[str]:
    """The single release-creation POST the run recorded."""
    calls = [command for command in recorder.commands if command[: len(RELEASE_POST_PREFIX)] == RELEASE_POST_PREFIX]
    assert len(calls) == 1, f"expected exactly one release POST, got {calls}"
    return calls[0]


def release_notes_field(call: list[str]) -> str:
    """The notes carried by a release-creation POST (its `description=` form field)."""
    prefix = "description="
    return next(argument for argument in call if argument.startswith(prefix)).removeprefix(prefix)


def test_release_opens_runway_and_current_milestones(recorder: CommandRecorder):
    green_repo(recorder)  # latest tag v1.2.3; a minor release cuts v1.3.0

    cli.invoke(app, ["release", "--minor"])

    # The runway milestone is two minors ahead; the released version's own milestone is opened too.
    assert ["glab", "milestone", "create", "--title", "v1.5.0"] in recorder.commands
    assert ["glab", "milestone", "create", "--title", "v1.3.0"] in recorder.commands


def test_release_patch_opens_no_milestone(recorder: CommandRecorder):
    green_repo(recorder)  # latest tag v1.2.3; a default (patch) release cuts v1.2.4

    cli.invoke(app, ["release"])

    assert not any(command[:3] == ["glab", "milestone", "create"] for command in recorder.commands)
    assert not any(argument.startswith("milestones[]=") for argument in release_create_call(recorder))
    assert not any("PUT" in command for command in recorder.commands)


def test_release_major_opens_runway_two_majors_ahead(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--major"])  # v1.2.3 -> v2.0.0

    assert ["glab", "milestone", "create", "--title", "v4.0.0"] in recorder.commands
    assert ["glab", "milestone", "create", "--title", "v2.0.0"] in recorder.commands


def test_release_skips_a_milestone_that_already_exists(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["milestones?title=v1.5.0"] = json.dumps([{"title": "v1.5.0"}])

    result = cli.invoke(app, ["release", "--minor"])

    assert "Milestone v1.5.0 already exists" in result.output
    assert ["glab", "milestone", "create", "--title", "v1.5.0"] not in recorder.commands


def test_release_milestone_queries_include_ancestor_group_milestones(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--minor"])

    assert ["glab", "api", "projects/:id/milestones?title=v1.3.0&include_parent_milestones=true"] in recorder.commands


def test_release_reuses_an_existing_group_milestone_instead_of_creating_it(recorder: CommandRecorder):
    green_repo(recorder)
    # GitLab rejects a project milestone shadowing a group one, so the group milestone must be
    # detected as existing and carried onto the release as-is.
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 66, "group_id": 9}])

    cli.invoke(app, ["release", "--minor"])

    assert ["glab", "milestone", "create", "--title", "v1.3.0"] not in recorder.commands
    assert "milestones[]=v1.3.0" in release_create_call(recorder)


def test_release_creates_gitlab_release_titled_and_milestoned_with_the_tag(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release", "--minor"])

    call = release_create_call(recorder)
    assert "tag_name=v1.3.0" in call
    assert "name=v1.3.0" in call
    assert "milestones[]=v1.3.0" in call
    # Never a ref: with only tag_name, GitLab can only attach the release to the pushed tag —
    # a stale replica fails the POST instead of attempting to create the protected tag.
    assert not any("ref" in argument for argument in call)


def test_release_notes_list_merge_requests_since_the_previous_tag(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "Ship the widget (!34)\n\nMerge (!12) into main\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["merge_requests/12"] = json.dumps({"iid": 12, "title": "Fix the gadget"})

    cli.invoke(app, ["release"])

    call = release_create_call(recorder)
    assert release_notes_field(call) == "- [!34] Add the widget\n- [!12] Fix the gadget"
    assert ["glab", "api", "projects/:id/merge_requests/34"] in recorder.commands


def test_release_notes_dedupe_references_and_drop_unfetchable_titles(recorder: CommandRecorder):
    green_repo(recorder)
    # !34 is referenced twice (deduplicated); !12 cannot be fetched, so it is dropped.
    recorder.captured_outputs["log --format"] = "Ship it (!34)\n\nMerge (!12) into main\nFollow-up (!34)\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.exit_codes["merge_requests/12"] = 1

    cli.invoke(app, ["release"])

    call = release_create_call(recorder)
    assert release_notes_field(call) == "- [!34] Add the widget"


def test_release_notes_are_empty_when_no_merge_requests_shipped(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "A direct push with no MR reference\n"

    cli.invoke(app, ["release"])

    call = release_create_call(recorder)
    assert release_notes_field(call) == ""


def test_release_publishes_without_a_milestone_when_it_cannot_be_opened(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.exit_codes["milestone create --title v1.3.0"] = 1  # the released version's own milestone

    result = cli.invoke(app, ["release", "--minor"])

    assert result.exit_code == 0
    assert "Could not open milestone v1.3.0" in result.output
    assert not any(argument.startswith("milestones[]=") for argument in release_create_call(recorder))
    assert "Released v1.3.0" in result.output


def test_release_reports_success_even_when_the_release_publish_fails(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.exit_codes["POST projects/:id/releases"] = 1

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "Could not create the GitLab release for v1.2.4" in result.output
    assert "Released v1.2.4" in result.output


def test_release_waits_for_the_tag_before_publishing(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release"])

    assert ["glab", "api", "projects/:id/repository/tags/v1.2.4"] in recorder.commands


def test_release_warns_but_still_publishes_when_the_tag_never_registers(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.exit_codes["repository/tags/"] = 1  # the API never reports the pushed tag

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "is not visible via the API yet" in result.output
    assert release_create_call(recorder)  # the release is still attempted rather than aborted


def test_release_milestones_shipped_mrs_and_leaves_manual_assignments(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "Ship the widget (!34)\n\nMerge (!12) into main\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["merge_requests/12"] = json.dumps(
        {"iid": 12, "title": "Fix the gadget", "milestone": {"title": "v9.9.9"}}
    )
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 77}])

    result = cli.invoke(app, ["release", "--minor"])

    assert result.exit_code == 0
    assert ["glab", "api", "-X", "PUT", "projects/:id/merge_requests/34", "-f", "milestone_id=77"] in recorder.commands
    assert "Assigned v1.3.0 to !34 Add the widget" in result.output
    # !12 was milestoned by hand (to a later version); the sweep must not overwrite it.
    assert not any("PUT" in command and "merge_requests/12" in " ".join(command) for command in recorder.commands)
    assert "!12 already belongs to v9.9.9; leaving it" in result.output


def test_release_sweep_warns_when_the_milestone_id_cannot_be_resolved(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "Ship the widget (!34)\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0"}])  # id missing

    result = cli.invoke(app, ["release", "--minor"])

    assert result.exit_code == 0
    assert "Could not resolve the id of milestone v1.3.0" in result.output
    assert not any("PUT" in command for command in recorder.commands)


def test_release_sweep_warns_but_continues_when_an_assignment_fails(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "Ship it (!34)\nFix it (!35)\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["merge_requests/35"] = json.dumps({"iid": 35, "title": "Polish the widget"})
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 77}])
    recorder.exit_codes["-X PUT projects/:id/merge_requests/34"] = 1

    result = cli.invoke(app, ["release", "--minor"])

    assert result.exit_code == 0
    assert "Could not assign v1.3.0 to !34" in result.output
    assert "Assigned v1.3.0 to !35 Polish the widget" in result.output
    assert "Released v1.3.0" in result.output


def test_release_skips_the_sweep_when_the_milestone_could_not_be_opened(recorder: CommandRecorder):
    green_repo(recorder)
    recorder.captured_outputs["log --format"] = "Ship it (!34)\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.exit_codes["milestone create --title v1.3.0"] = 1

    result = cli.invoke(app, ["release", "--minor"])

    assert result.exit_code == 0
    assert not any("PUT" in command for command in recorder.commands)


def test_create_release_retries_until_the_tag_registers(monkeypatch: pytest.MonkeyPatch):
    """A POST that fails while GitLab's replicas have not caught up with the pushed tag is retried."""
    responses = iter([(1, "tag not yet visible"), (0, "{}")])
    calls: list[list[str]] = []

    def fake_run_quiet(args: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    release.create_release("v1.2.4", "notes", None)

    assert len(calls) == 2


def test_merge_request_is_none_on_an_unparseable_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (0, "not-json"))

    assert release.merge_request(7) is None


def test_milestone_exists_is_false_when_the_query_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (1, "boom"))

    assert release.milestone_exists("v1.0.0") is False


def test_release_first_ever_tag_is_v0_1_0(recorder: CommandRecorder):
    green_repo(recorder, tags="")

    cli.invoke(app, ["release"])

    assert ["git", "tag", "-a", "v0.1.0", "abc123def456", "-m", "v0.1.0"] in recorder.commands


def test_release_ignores_non_semver_tags(recorder: CommandRecorder):
    green_repo(recorder, tags="v1.0\nvnightly\nv2.4.9\nv2.4.10\n")

    cli.invoke(app, ["release"])

    assert ["git", "tag", "-a", "v2.4.11", "abc123def456", "-m", "v2.4.11"] in recorder.commands


def test_release_reads_the_previous_version_from_origin_not_local_tags(recorder: CommandRecorder):
    green_repo(recorder)  # origin knows v1.2.3 only
    # A stray local-only tag at a higher version must not shift the changelog range.
    recorder.captured_outputs["tag --list"] = "v1.2.3\nv9.9.9\n"

    cli.invoke(app, ["release"])

    assert ["git", "ls-remote", "--tags", "origin", "v*"] in recorder.commands
    assert ["git", "tag", "-a", "v1.2.4", "abc123def456", "-m", "v1.2.4"] in recorder.commands


@pytest.mark.parametrize("flags", [["--minor", "--major"], ["--patch", "--minor"], ["--patch", "--major"]])
def test_release_rejects_combined_bump_flags(recorder: CommandRecorder, flags: list[str]):
    green_repo(recorder)

    result = cli.invoke(app, ["release", *flags])

    assert result.exit_code != 0
    assert "at most one of --patch/--minor/--major" in result.output
    # The conflict aborts before the fetch, so no git or glab command runs at all.
    assert not recorder.commands


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


def test_pipeline_url_for_ref_retries_past_a_failed_or_unparseable_poll(monkeypatch: pytest.MonkeyPatch):
    responses = iter(
        [(1, "boom"), (0, "not-json"), (0, json.dumps([{"id": 5, "status": "created", "web_url": "https://x/5"}]))]
    )
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: next(responses))
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.pipeline_url_for_ref("v1.2.4") == "https://x/5"


def test_wait_for_tag_requires_consecutive_consistent_reads(monkeypatch: pytest.MonkeyPatch):
    """The poll returns True only after the tag reads back the required number of times in a row."""
    found = (0, json.dumps({"name": "v1.2.4"}))
    responses = iter([(1, "404"), *([found] * release.TAG_POLL_CONSECUTIVE_SUCCESSES)])
    calls: list[list[str]] = []

    def fake_run_quiet(args: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.wait_for_tag("v1.2.4") is True
    assert len(calls) == 1 + release.TAG_POLL_CONSECUTIVE_SUCCESSES


def test_wait_for_tag_resets_the_streak_on_a_stale_read(monkeypatch: pytest.MonkeyPatch):
    """A failed read between successes restarts the consecutive count — one hit on a fresh replica
    while others are stale must not release the poll early."""
    found = (0, json.dumps({"name": "v1.2.4"}))
    responses = iter([found, (1, "404"), *([found] * release.TAG_POLL_CONSECUTIVE_SUCCESSES)])
    calls: list[list[str]] = []

    def fake_run_quiet(args: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.wait_for_tag("v1.2.4") is True
    assert len(calls) == 2 + release.TAG_POLL_CONSECUTIVE_SUCCESSES


def test_wait_for_tag_gives_up_after_the_poll_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (1, "404"))
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)

    assert release.wait_for_tag("v1.2.4") is False


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
        (None, "minor", (0, 1, 0)),
        (None, "major", (0, 1, 0)),
    ],
)
def test_next_version(current: release.Version | None, bump: str, expected: release.Version):
    assert release.next_version(current, bump) == expected


@pytest.mark.parametrize(
    ("version", "bump", "expected"),
    [
        ((1, 4, 2), "minor", (1, 6, 0)),
        ((1, 4, 2), "major", (3, 0, 0)),
        ((0, 1, 0), "minor", (0, 3, 0)),
    ],
)
def test_second_next_version(version: release.Version, bump: str, expected: release.Version):
    assert release.second_next_version(version, bump) == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v1.2.3", (1, 2, 3)), ("v0.0.0", (0, 0, 0)), ("v1.2", None), ("v1.2.3-rc1", None), ("nightly", None)],
)
def test_parse_version(tag: str, expected: release.Version | None):
    assert release.parse_version(tag) == expected


def test_format_version():
    assert release.format_version((2, 0, 5)) == "v2.0.5"
