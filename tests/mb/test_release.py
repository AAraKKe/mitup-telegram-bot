import json
from datetime import date
from pathlib import Path

import pytest
from command_recording import CommandRecorder
from mb.main import app
from typer.testing import CliRunner

from mb import crowdin_mr_ops, release, runner

cli = CliRunner()

TAG_PIPELINE_URL = "https://gitlab.com/meetupbot/mitup-telegram-bot/-/pipelines/42"
MR_URL = "https://gitlab.com/meetupbot/mitup-telegram-bot/-/merge_requests"
HEAD = "abc123def456"
RELEASE_MR_URL = f"{MR_URL}/800"
CROWDIN_FRAGMENT = "+crowdin-translations-20261114090000.internal.md"
RENOVATE_FRAGMENT = "+renovate-ruff-20261114090000.internal.md"
WHATS_NEW_PAGE = (
    "# What's new\n\nIntro.\n\n<!-- releases -->\n\n## v1.2.3\n\n_1 November 2026_\n\nYou can pin meetings.\n"
)
COMPILED_CHANGELOG = (
    "# Changelog\n\n<!-- towncrier release notes start -->\n\n## v2.0.0 (2026-11-14)\n\n### New\n\n"
    f"- You can open What's new from the main menu. ([!790]({MR_URL}/790))\n\n### Under the hood\n\n"
    f"- CI images are pinned to a digest. ([!775]({MR_URL}/775))\n\n## v1.2.3 (2026-11-01)\n\n### Fixed\n\n"
    f"- Reminders arrive on time. ([!12]({MR_URL}/12))\n"
)


class FixedDate(date):
    @classmethod
    def today(cls) -> FixedDate:
        return cls(2026, 11, 14)


@pytest.fixture(autouse=True)
def plain_wide_console(monkeypatch: pytest.MonkeyPatch):
    """Force plain, unwrapped output so assertions can match whole lines."""
    monkeypatch.setenv("MB_PLAIN", "1")
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def no_poll_sleep(monkeypatch: pytest.MonkeyPatch):
    """Never actually sleep between pipeline polls; the tests drive the responses directly."""
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)


def tag_listing(tags: str) -> str:
    """Origin's `ls-remote` listing for *tags*, each with its peeled `^{}` companion line."""
    return "".join(
        f"cafe{index:04x}\trefs/tags/{tag}\ncafe{index:04x}\trefs/tags/{tag}^{{}}\n"
        for index, tag in enumerate(tags.split())
    )


def prepared_changelog(version: str) -> str:
    return (
        f"# Changelog\n\n<!-- towncrier release notes start -->\n\n## {version} (2026-11-14)\n\n### Fixed\n\n"
        f"- Reminders arrive on time. ([!34]({MR_URL}/34))\n\n## v1.2.3 (2026-11-01)\n\n### Fixed\n\n- Old.\n"
    )


def green_repo(
    recorder: CommandRecorder, *, version: str = "v1.2.4", tags: str = "v1.2.3\n", head: str = HEAD
) -> CommandRecorder:
    """Prime the recorder with *version* prepared on origin/main and landed at the green commit *head*.

    The pre-release green check filters pipelines by `sha`; the post-push watch filters by `ref`, so
    the two queries are keyed separately and a test can override one without disturbing the other.
    """
    recorder.captured_outputs.update(
        {
            "show origin/main:CHANGELOG.md": prepared_changelog(version),
            "log -1 --format=%H": head,
            "rev-list --count": "0",
            f"show {head}:docs/whats-new/index.md": f"<!-- releases -->\n\n## {version}\n\nYou can pin meetings.\n",
            "pipelines?sha=": json.dumps([{"id": 7, "status": "success"}]),
            "pipelines?ref=": json.dumps([{"id": 42, "status": "created", "web_url": TAG_PIPELINE_URL}]),
            "ls-remote --tags": tag_listing(tags),
        }
    )
    return recorder


def pending_repo(
    recorder: CommandRecorder,
    *fragments: str,
    tags: str = "v1.2.3\n",
    fragment_text: str = "A fragment users can read.",
    subjects: dict[str, str] | None = None,
) -> CommandRecorder:
    """Prime the recorder with *fragments* pending at origin/main and v1.2.3 as the newest released version.

    *subjects* maps a fragment name to the subject of the commit that added it.
    """
    recorder.captured_outputs.update(
        {
            "rev-parse origin/main": HEAD,
            "ls-tree --name-only": "".join(f"changelog.d/{name}\n" for name in ("README.md", *fragments)),
            f"show {HEAD}:changelog.d/": fragment_text,
            f"show {HEAD}:CHANGELOG.md": "# Changelog\n\n<!-- towncrier release notes start -->\n",
            f"show {HEAD}:docs/whats-new/index.md": WHATS_NEW_PAGE,
            "mr create": f"{RELEASE_MR_URL}\n",
            "ls-remote --tags": tag_listing(tags),
            **{f"-- changelog.d/{name}": subject for name, subject in (subjects or {}).items()},
        }
    )
    return recorder


@pytest.fixture
def written_pages(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Back `git worktree add` with a real checkout holding the compiled CHANGELOG.md and the pending fragments,
    apply each successful `git mv` there, and collect the What's new page as each `git commit` sees it."""
    pages: list[str] = []

    def run_quiet(
        args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        if args[:3] == ["git", "worktree", "add"]:
            checkout = Path(args[4])
            (checkout / "docs" / "whats-new").mkdir(parents=True)
            (checkout / "CHANGELOG.md").write_text(COMPILED_CHANGELOG)
            (checkout / "changelog.d").mkdir()
            for path in recorder.captured_outputs["ls-tree --name-only"].splitlines():
                (checkout / path).write_text(recorder.captured_outputs[f"show {HEAD}:changelog.d/"])
        if args[:2] == ["git", "commit"] and cwd is not None:
            pages.append((cwd / release.WHATS_NEW_PAGE).read_text())
        exit_code, captured = recorder.run_quiet(args, cwd=cwd, extra_env=extra_env)
        if args[:2] == ["git", "mv"] and cwd is not None and exit_code == 0:
            (cwd / args[2]).rename(cwd / args[3])
        return exit_code, captured

    monkeypatch.setattr(runner, "run_quiet", run_quiet)
    monkeypatch.setattr(release, "date", FixedDate)
    return pages


@pytest.fixture
def compiled_fragments(written_pages: list[str], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Collect the checkout's fragment files, by name, as towncrier sees them."""
    fragments: dict[str, str] = {}
    run_quiet = runner.run_quiet

    def snapshot_on_compile(
        args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        if "towncrier" in args and cwd is not None:
            fragments.update({path.name: path.read_text() for path in (cwd / "changelog.d").iterdir()})
        return run_quiet(args, cwd=cwd, extra_env=extra_env)

    monkeypatch.setattr(runner, "run_quiet", snapshot_on_compile)
    return fragments


def ran(recorder: CommandRecorder, prefix: list[str]) -> bool:
    return any(command[: len(prefix)] == prefix for command in recorder.commands)


# --- mb release prepare ---


@pytest.mark.parametrize(
    ("fragments", "expected_version"),
    [
        (["12.feature.md", "13.fix.md"], "v2.0.0"),
        (["12.removal.md"], "v2.0.0"),
        (["12.change.md", "13.internal.md"], "v1.3.0"),
        (["12.fix.md", "13.internal.md"], "v1.2.4"),
        (["12.internal.md"], "v1.2.4"),
    ],
)
def test_prepare_computes_the_version_from_the_fragment_types(
    recorder: CommandRecorder, fragments: list[str], expected_version: str
):
    pending_repo(recorder, *fragments)

    result = cli.invoke(app, ["release", "prepare"])

    assert f"Version to prepare: {expected_version}" in result.output


def test_prepare_shows_the_plan_and_changes_nothing_without_confirmation(recorder: CommandRecorder):
    pending_repo(recorder, "12.change.md", "13.fix.md", "14.internal.md")

    result = cli.invoke(app, ["release", "prepare"])

    assert result.exit_code != 0
    assert "Previous version: v1.2.3" in result.output
    assert "Version to prepare: v1.3.0 (minor)" in result.output
    assert "Pending fragments: 0 feature, 0 removal, 1 change, 1 fix, 1 internal" in result.output
    assert "  12.change.md: A fragment users can read." in result.output
    assert "13.fix.md" not in result.output
    assert not ran(recorder, ["git", "worktree"])
    assert not ran(recorder, ["git", "push"])
    assert not ran(recorder, ["glab", "mr"])


@pytest.mark.parametrize(
    ("flag", "fragment", "expected"),
    [
        ("--patch", "12.feature.md", "The fragments call for v2.0.0 (major); the flag chooses v1.2.4."),
        ("--minor", "12.fix.md", "The fragments call for v1.2.4 (patch); the flag chooses v1.3.0."),
        ("--major", "12.change.md", "The fragments call for v1.3.0 (minor); the flag chooses v2.0.0."),
    ],
)
def test_prepare_flag_overrides_the_computed_version(
    recorder: CommandRecorder, flag: str, fragment: str, expected: str
):
    pending_repo(recorder, fragment)

    result = cli.invoke(app, ["release", "prepare", flag])

    assert expected in result.output


def test_prepare_aborts_without_pending_fragments(recorder: CommandRecorder):
    pending_repo(recorder)

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert "there is nothing to release" in result.output
    assert not ran(recorder, ["git", "worktree"])


@pytest.mark.parametrize("flags", [["--minor", "--major"], ["--patch", "--minor"], ["--patch", "--major"]])
def test_prepare_rejects_combined_bump_flags(recorder: CommandRecorder, flags: list[str]):
    pending_repo(recorder, "12.fix.md")

    result = cli.invoke(app, ["release", "prepare", *flags])

    assert result.exit_code != 0
    assert "at most one of --patch/--minor/--major" in result.output
    assert not recorder.commands


def test_prepare_refuses_while_a_prepared_version_is_untagged(recorder: CommandRecorder):
    pending_repo(recorder, "12.fix.md")
    recorder.captured_outputs[f"show {HEAD}:CHANGELOG.md"] = prepared_changelog("v1.2.4")

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert "v1.2.4 is prepared but not tagged: run `uv run mb release` first." in result.output
    assert not ran(recorder, ["git", "worktree"])


@pytest.mark.parametrize(
    ("exit_code", "page", "expected_message"),
    [
        (1, "", "Commit abc123de has no docs/whats-new/index.md."),
        (0, "# What's new\n", "docs/whats-new/index.md has no `<!-- releases -->` line"),
    ],
    ids=["missing-page", "missing-marker"],
)
def test_prepare_refuses_a_whats_new_page_it_cannot_extend(
    recorder: CommandRecorder, exit_code: int, page: str, expected_message: str
):
    pending_repo(recorder, "12.fix.md")
    recorder.exit_codes[f"show {HEAD}:docs/whats-new/index.md"] = exit_code
    recorder.captured_outputs[f"show {HEAD}:docs/whats-new/index.md"] = page

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert expected_message in result.output
    assert not ran(recorder, ["git", "worktree"])


def test_prepare_commits_the_release_on_a_branch_and_opens_the_merge_request(
    recorder: CommandRecorder, written_pages: list[str]
):
    pending_repo(recorder, "790.feature.md", "775.internal.md")

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code == 0, result.output
    towncrier = next(call for call in recorder.calls if "towncrier" in call.args)
    checkout = towncrier.cwd
    assert checkout is not None
    assert towncrier.args[2:] == ["towncrier", "build", "--version", "v2.0.0", "--date", "2026-11-14", "--yes"]
    assert ["git", "worktree", "add", "--detach", str(checkout), HEAD] in recorder.commands
    assert ["git", "commit", "--no-verify", "--message", "⚙️ Release v2.0.0"] in recorder.commands
    assert ["git", "push", "--no-verify", "origin", "HEAD:refs/heads/release/v2.0.0"] in recorder.commands
    assert ["git", "worktree", "remove", "--force", str(checkout)] in recorder.commands
    merge_request = next(command for command in recorder.commands if command[:3] == ["glab", "mr", "create"])
    assert merge_request[merge_request.index("--source-branch") + 1] == "release/v2.0.0"
    assert merge_request[merge_request.index("--title") + 1] == "⚙️ Release v2.0.0"
    assert "uv run mb release" in merge_request[merge_request.index("--description") + 1]
    assert RELEASE_MR_URL in result.output
    assert written_pages == [
        "# What's new\n\nIntro.\n\n<!-- releases -->\n\n## v2.0.0\n\n_14 November 2026_\n\n"
        '<!-- release-summary -->\n\n??? note "Full changelog"\n\n    **New**\n\n'
        f"    - You can open What's new from the main menu. ([!790]({MR_URL}/790))\n\n    **Under the hood**\n\n"
        f"    - CI images are pinned to a digest. ([!775]({MR_URL}/775))\n\n"
        "## v1.2.3\n\n_1 November 2026_\n\nYou can pin meetings.\n"
    ]


@pytest.mark.usefixtures("written_pages")
def test_prepare_removes_the_temporary_checkout_when_a_step_fails(recorder: CommandRecorder):
    pending_repo(recorder, "12.fix.md")
    recorder.exit_codes["towncrier"] = 1

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert ran(recorder, ["git", "worktree", "remove", "--force"])
    assert not ran(recorder, ["git", "push"])
    assert not ran(recorder, ["glab", "mr"])


@pytest.mark.parametrize(
    ("fragments", "subjects", "expected_renames"),
    [
        pytest.param(
            ["12.fix.md", CROWDIN_FRAGMENT],
            {CROWDIN_FRAGMENT: "🗣️ Update approved translations from Crowdin (!801)"},
            [f"{CROWDIN_FRAGMENT} -> 801.internal.md"],
            id="number-free",
        ),
        pytest.param(
            ["792.internal.md", RENOVATE_FRAGMENT],
            {RENOVATE_FRAGMENT: "⬆️ Update ruff (!792)"},
            [f"{RENOVATE_FRAGMENT} -> 792.internal.1.md"],
            id="number-taken-by-a-pending-fragment",
        ),
        pytest.param(
            ["792.internal.md", "792.internal.1.md", RENOVATE_FRAGMENT],
            {RENOVATE_FRAGMENT: "⬆️ Update ruff (!792)"},
            [f"{RENOVATE_FRAGMENT} -> 792.internal.2.md"],
            id="counter-taken-too",
        ),
        pytest.param(
            [CROWDIN_FRAGMENT, RENOVATE_FRAGMENT],
            {CROWDIN_FRAGMENT: "Squashed (!801)", RENOVATE_FRAGMENT: "Squashed (!801)"},
            [f"{CROWDIN_FRAGMENT} -> 801.internal.md", f"{RENOVATE_FRAGMENT} -> 801.internal.1.md"],
            id="two-from-one-merge-request",
        ),
        pytest.param(
            ["801.internal.md", "+tidy-ci.fix.md"],
            {"+tidy-ci.fix.md": "🐛 Tidy CI (!801)"},
            ["+tidy-ci.fix.md -> 801.fix.md"],
            id="other-type-of-the-same-merge-request",
        ),
        pytest.param(
            [RENOVATE_FRAGMENT],
            {RENOVATE_FRAGMENT: "Revert the release (!811)\n⬆️ Update ruff (!791)"},
            [f"{RENOVATE_FRAGMENT} -> 791.internal.md"],
            id="re-added-by-a-later-merge-request",
        ),
    ],
)
def test_prepare_plan_numbers_each_unnumbered_fragment_and_changes_nothing_without_confirmation(
    recorder: CommandRecorder, fragments: list[str], subjects: dict[str, str], expected_renames: list[str]
):
    pending_repo(recorder, *fragments, subjects=subjects)

    result = cli.invoke(app, ["release", "prepare"])

    assert result.exit_code != 0
    assert "Fragments written before their merge request existed take its number:" in result.output
    for rename in expected_renames:
        assert f"  {rename}\n" in result.output
    assert not ran(recorder, ["git", "worktree"])
    assert not ran(recorder, ["git", "push"])
    assert not ran(recorder, ["glab", "mr"])


def test_prepare_plan_lists_no_renames_when_every_fragment_is_numbered(recorder: CommandRecorder):
    pending_repo(recorder, "12.fix.md")

    result = cli.invoke(app, ["release", "prepare"])

    assert "take its number" not in result.output
    assert not ran(recorder, ["git", "log"])


def test_prepare_plan_shows_a_deciding_fragment_without_its_comment(recorder: CommandRecorder):
    pending_repo(
        recorder,
        CROWDIN_FRAGMENT,
        fragment_text=crowdin_mr_ops.FRAGMENT_TEXT,
        subjects={CROWDIN_FRAGMENT: "🗣️ Update approved translations from Crowdin (!801)"},
    )

    result = cli.invoke(app, ["release", "prepare"])

    assert f"  {CROWDIN_FRAGMENT}: Translations are updated from Crowdin.\n" in result.output
    assert "<!--" not in result.output


def test_prepare_numbers_unnumbered_fragments_in_the_checkout_before_compiling(
    recorder: CommandRecorder, compiled_fragments: dict[str, str]
):
    pending_repo(
        recorder,
        "790.feature.md",
        CROWDIN_FRAGMENT,
        fragment_text=crowdin_mr_ops.FRAGMENT_TEXT,
        subjects={CROWDIN_FRAGMENT: "🗣️ Update approved translations from Crowdin (!801)"},
    )

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code == 0, result.output
    assert [
        "git",
        "log",
        "--diff-filter=A",
        "--format=%s",
        HEAD,
        "--",
        f"changelog.d/{CROWDIN_FRAGMENT}",
    ] in recorder.commands
    towncrier = next(call for call in recorder.calls if "towncrier" in call.args)
    move = next(call for call in recorder.calls if call.args[:2] == ["git", "mv"])
    assert move.args == ["git", "mv", f"changelog.d/{CROWDIN_FRAGMENT}", "changelog.d/801.internal.md"]
    assert move.cwd == towncrier.cwd
    assert compiled_fragments == {
        "README.md": crowdin_mr_ops.FRAGMENT_TEXT,
        "790.feature.md": "Translations are updated from Crowdin.\n",
        "801.internal.md": "Translations are updated from Crowdin.\n",
    }


@pytest.mark.parametrize(
    ("fragment_text", "expected_compiled_text"),
    [
        pytest.param(crowdin_mr_ops.FRAGMENT_TEXT, "Translations are updated from Crowdin.\n", id="opening-comment"),
        pytest.param(
            "You can pin meetings.\n<!-- Owners only. -->\n",
            "You can pin meetings.\n<!-- Owners only. -->\n",
            id="comment-mid-text",
        ),
    ],
)
def test_prepare_compiles_numbered_fragments_without_their_opening_comment(
    recorder: CommandRecorder, compiled_fragments: dict[str, str], fragment_text: str, expected_compiled_text: str
):
    pending_repo(recorder, "790.feature.md", fragment_text=fragment_text)

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code == 0, result.output
    assert compiled_fragments["790.feature.md"] == expected_compiled_text


@pytest.mark.parametrize(
    "subject",
    [
        pytest.param("", id="no-commit-found"),
        pytest.param("⬆️ Update ruff !12", id="reference-without-parentheses"),
        pytest.param("⬆️ Update ruff (!12) and ty", id="reference-mid-subject"),
        pytest.param("Merge branch 'renovate/ruff' into 'main'", id="no-reference"),
    ],
)
def test_prepare_aborts_when_the_merge_request_of_an_unnumbered_fragment_is_unknown(
    recorder: CommandRecorder, subject: str
):
    pending_repo(recorder, "12.fix.md", RENOVATE_FRAGMENT, subjects={RENOVATE_FRAGMENT: subject})

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert f"Cannot tell which merge request added changelog.d/{RENOVATE_FRAGMENT}." in result.output
    assert f"must have a subject ending in `(!123)`; found {subject!r}." in result.output
    assert "Rename it to <MR number>.internal.md in a merge request" in result.output
    assert "Version to prepare" not in result.output
    assert not ran(recorder, ["git", "worktree"])
    assert not ran(recorder, ["git", "push"])
    assert not ran(recorder, ["glab", "mr"])


@pytest.mark.usefixtures("written_pages")
def test_prepare_aborts_and_removes_the_checkout_when_numbering_a_fragment_fails(recorder: CommandRecorder):
    pending_repo(recorder, RENOVATE_FRAGMENT, subjects={RENOVATE_FRAGMENT: "⬆️ Update ruff (!801)"})
    recorder.exit_codes["git mv"] = 1

    result = cli.invoke(app, ["release", "prepare", "--yes"])

    assert result.exit_code != 0
    assert f"Numbering {RENOVATE_FRAGMENT} as 801.internal.md failed" in result.output
    assert ran(recorder, ["git", "worktree", "remove", "--force"])
    assert not any("towncrier" in command for command in recorder.commands)
    assert not ran(recorder, ["git", "push"])
    assert not ran(recorder, ["glab", "mr"])


def test_prepare_ignores_non_semver_tags(recorder: CommandRecorder):
    pending_repo(recorder, "12.fix.md", tags="v1.0\nvnightly\nv2.4.9\nv2.4.10\n")

    result = cli.invoke(app, ["release", "prepare"])

    assert "Version to prepare: v2.4.11" in result.output


def test_prepare_reads_the_previous_version_from_origin_not_local_tags(recorder: CommandRecorder):
    pending_repo(recorder, "12.fix.md")  # origin knows v1.2.3 only
    recorder.captured_outputs["tag --list"] = "v1.2.3\nv9.9.9\n"

    result = cli.invoke(app, ["release", "prepare"])

    assert ["git", "ls-remote", "--tags", "origin", "v*"] in recorder.commands
    assert "Version to prepare: v1.2.4" in result.output


# --- mb release ---


def test_release_tags_and_pushes_the_prepared_version(recorder: CommandRecorder):
    green_repo(recorder)

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0, result.output
    assert ["git", "fetch", "--tags", "origin"] in recorder.commands
    assert ["git", "tag", "-a", "v1.2.4", HEAD, "-m", "v1.2.4"] in recorder.commands
    assert ["git", "push", "origin", "v1.2.4"] in recorder.commands
    assert ["glab", "api", "projects/:id/pipelines?ref=v1.2.4"] in recorder.commands
    assert "Released v1.2.4" in result.output
    assert f"Deploy pipeline started: {TAG_PIPELINE_URL}" in result.output


def test_release_tags_the_commit_that_last_changed_the_changelog(recorder: CommandRecorder):
    green_repo(recorder, head="c4a9e1f00000")
    recorder.captured_outputs["rev-list --count"] = "2"

    result = cli.invoke(app, ["release"])

    assert ["git", "log", "-1", "--format=%H", "origin/main", "--", "CHANGELOG.md"] in recorder.commands
    assert ["glab", "api", "projects/:id/pipelines?sha=c4a9e1f00000"] in recorder.commands
    assert ["git", "tag", "-a", "v1.2.4", "c4a9e1f00000", "-m", "v1.2.4"] in recorder.commands
    assert "origin/main has 2 commits after c4a9e1f0; they ship in the next release." in result.output


@pytest.mark.parametrize(
    ("outputs", "expected_message"),
    [
        pytest.param({"ls-remote --tags": tag_listing("v1.2.3 v1.2.4")}, "Nothing is prepared", id="already-tagged"),
        pytest.param(
            {f"show {HEAD}:docs/whats-new/index.md": "<!-- releases -->\n\n## v1.2.3\n"},
            "docs/whats-new/index.md at abc123de has no section for v1.2.4.",
            id="no-whats-new-section",
        ),
        pytest.param(
            {f"show {HEAD}:docs/whats-new/index.md": "<!-- releases -->\n\n## v1.2.4\n\n<!-- release-summary -->\n"},
            "still holds <!-- release-summary -->",
            id="summary-not-written",
        ),
    ],
)
def test_release_refuses_an_unready_release(recorder: CommandRecorder, outputs: dict[str, str], expected_message: str):
    green_repo(recorder)
    recorder.captured_outputs.update(outputs)

    result = cli.invoke(app, ["release"])

    assert result.exit_code != 0
    assert expected_message in result.output
    assert not ran(recorder, ["git", "tag"])


RELEASE_POST_PREFIX = ["glab", "api", "-X", "POST", "projects/:id/releases"]


def release_create_call(recorder: CommandRecorder) -> list[str]:
    """The single release-creation POST the run recorded."""
    calls = [command for command in recorder.commands if command[: len(RELEASE_POST_PREFIX)] == RELEASE_POST_PREFIX]
    assert len(calls) == 1, f"expected exactly one release POST, got {calls}"
    return calls[0]


def test_release_description_is_the_changelog_section(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release"])

    call = release_create_call(recorder)
    assert f"description=### Fixed\n\n- Reminders arrive on time. ([!34]({MR_URL}/34))" in call


def test_release_opens_runway_and_current_milestones(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")

    cli.invoke(app, ["release"])

    # The runway milestone is two minors ahead; the released version's own milestone is opened too.
    assert ["glab", "milestone", "create", "--title", "v1.5.0"] in recorder.commands
    assert ["glab", "milestone", "create", "--title", "v1.3.0"] in recorder.commands


def test_release_patch_opens_no_milestone(recorder: CommandRecorder):
    green_repo(recorder)

    cli.invoke(app, ["release"])

    assert not ran(recorder, ["glab", "milestone", "create"])
    assert not any(argument.startswith("milestones[]=") for argument in release_create_call(recorder))
    assert not any(
        argument.startswith("projects/:id/merge_requests/") for command in recorder.commands for argument in command
    )


def test_release_major_opens_runway_two_majors_ahead(recorder: CommandRecorder):
    green_repo(recorder, version="v2.0.0")

    cli.invoke(app, ["release"])

    assert ["glab", "milestone", "create", "--title", "v4.0.0"] in recorder.commands
    assert ["glab", "milestone", "create", "--title", "v2.0.0"] in recorder.commands


def test_release_skips_a_milestone_that_already_exists(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")
    recorder.captured_outputs["milestones?title=v1.5.0"] = json.dumps([{"title": "v1.5.0"}])

    result = cli.invoke(app, ["release"])

    assert "Milestone v1.5.0 already exists" in result.output
    assert ["glab", "milestone", "create", "--title", "v1.5.0"] not in recorder.commands


def test_release_milestone_queries_include_ancestor_group_milestones(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")

    cli.invoke(app, ["release"])

    assert ["glab", "api", "projects/:id/milestones?title=v1.3.0&include_parent_milestones=true"] in recorder.commands


def test_release_reuses_an_existing_group_milestone_instead_of_creating_it(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")
    # GitLab rejects a project milestone shadowing a group one, so the group milestone must be
    # detected as existing and carried onto the release as-is.
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 66, "group_id": 9}])

    cli.invoke(app, ["release"])

    assert ["glab", "milestone", "create", "--title", "v1.3.0"] not in recorder.commands
    assert "milestones[]=v1.3.0" in release_create_call(recorder)


def test_release_creates_gitlab_release_titled_and_milestoned_with_the_tag(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")

    cli.invoke(app, ["release"])

    call = release_create_call(recorder)
    assert "tag_name=v1.3.0" in call
    assert "name=v1.3.0" in call
    assert "milestones[]=v1.3.0" in call
    # Never a ref: with only tag_name, GitLab can only attach the release to the pushed tag, so a
    # stale replica fails the POST instead of attempting to create the protected tag.
    assert not any(argument.startswith("ref=") for argument in call)


def test_release_publishes_without_a_milestone_when_it_cannot_be_opened(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")
    recorder.exit_codes["milestone create --title v1.3.0"] = 1  # the released version's own milestone

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "Could not open milestone v1.3.0" in result.output
    assert not any(argument.startswith("milestones[]=") for argument in release_create_call(recorder))
    assert not any("PUT" in command for command in recorder.commands)
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
    green_repo(recorder, version="v1.3.0")
    recorder.captured_outputs["log --format"] = "Ship the widget (!34)\n\x00Merge (!12) into main\n\x00Gone (!13)\n\x00"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["merge_requests/12"] = json.dumps(
        {"iid": 12, "title": "Fix the gadget", "milestone": {"title": "v9.9.9"}}
    )
    recorder.exit_codes["merge_requests/13"] = 1
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 77}])

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert ["git", "log", "--format=%B%x00", f"v1.2.3..{HEAD}"] in recorder.commands
    assert ["glab", "api", "-X", "PUT", "projects/:id/merge_requests/34", "-f", "milestone_id=77"] in recorder.commands
    assert "Assigned v1.3.0 to !34 Add the widget" in result.output
    # !12 was milestoned by hand (to a later version) and !13 cannot be fetched; neither is touched.
    assert not any("PUT" in command and "merge_requests/12" in " ".join(command) for command in recorder.commands)
    assert not any("PUT" in command and "merge_requests/13" in " ".join(command) for command in recorder.commands)
    assert "!12 already belongs to v9.9.9; leaving it" in result.output


def test_release_sweep_warns_when_the_milestone_id_cannot_be_resolved(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")
    recorder.captured_outputs["log --format"] = "Ship the widget (!34)\n"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0"}])  # id missing

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "Could not resolve the id of milestone v1.3.0" in result.output
    assert not any("PUT" in command for command in recorder.commands)


def test_release_sweep_warns_but_continues_when_an_assignment_fails(recorder: CommandRecorder):
    green_repo(recorder, version="v1.3.0")
    recorder.captured_outputs["log --format"] = "Ship it (!34)\n\x00Fix it (!35)\n\x00"
    recorder.captured_outputs["merge_requests/34"] = json.dumps({"iid": 34, "title": "Add the widget"})
    recorder.captured_outputs["merge_requests/35"] = json.dumps({"iid": 35, "title": "Polish the widget"})
    recorder.captured_outputs["milestones?title=v1.3.0"] = json.dumps([{"title": "v1.3.0", "id": 77}])
    recorder.exit_codes["-X PUT projects/:id/merge_requests/34"] = 1

    result = cli.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "Could not assign v1.3.0 to !34" in result.output
    assert "Assigned v1.3.0 to !35 Polish the widget" in result.output
    assert "Released v1.3.0" in result.output


def test_merge_request_ids_count_one_merge_request_per_commit(recorder: CommandRecorder):
    # A feature-branch squash embeds the messages of the commits it squashed, each with its own MR,
    # and a merge request referenced by two commits counts once.
    feature_squash = (
        "Rebuild the bot on rich messages (!70)\n\nSee merge request group/project!70\n\n"
        "Commits squashed (max 100):\n\n* Add the payload layer (!66)\n\nSee merge request group/project!66\n"
    )
    recorder.captured_outputs["log --format"] = feature_squash + "\x00Fix the gadget (!12)\n\x00Follow-up (!70)\n\x00"

    assert release.merge_request_ids("v1.2.3", HEAD) == [70, 12]


def test_create_release_retries_until_the_tag_registers(monkeypatch: pytest.MonkeyPatch):
    """A POST that fails while GitLab's replicas have not caught up with the pushed tag is retried."""
    responses = iter([(1, "tag not yet visible"), (0, "{}")])
    calls: list[list[str]] = []

    def fake_run_quiet(args: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)

    release.create_release("v1.2.4", "notes", None)

    assert len(calls) == 2


def test_merge_request_is_none_on_an_unparseable_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (0, "not-json"))

    assert release.merge_request(7) is None


def test_milestone_exists_is_false_when_the_query_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (1, "boom"))

    assert release.milestone_exists("v1.0.0") is False


def test_release_tags_origin_main_regardless_of_local_state(recorder: CommandRecorder):
    green_repo(recorder, head="999fedcba000")
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

    assert release.pipeline_url_for_ref("v1.2.4") == "https://x/5"


def test_pipeline_url_for_ref_gives_up_after_the_poll_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (0, "[]"))

    assert release.pipeline_url_for_ref("v1.2.4") is None


def test_pipeline_url_for_ref_retries_past_a_failed_or_unparseable_poll(monkeypatch: pytest.MonkeyPatch):
    responses = iter(
        [(1, "boom"), (0, "not-json"), (0, json.dumps([{"id": 5, "status": "created", "web_url": "https://x/5"}]))]
    )
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: next(responses))

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

    assert release.wait_for_tag("v1.2.4") is True
    assert len(calls) == 1 + release.TAG_POLL_CONSECUTIVE_SUCCESSES


def test_wait_for_tag_resets_the_streak_on_a_stale_read(monkeypatch: pytest.MonkeyPatch):
    """A failed read between successes restarts the consecutive count, so one hit on a fresh replica
    while others are stale does not release the poll early."""
    found = (0, json.dumps({"name": "v1.2.4"}))
    responses = iter([found, (1, "404"), *([found] * release.TAG_POLL_CONSECUTIVE_SUCCESSES)])
    calls: list[list[str]] = []

    def fake_run_quiet(args: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(runner, "run_quiet", fake_run_quiet)

    assert release.wait_for_tag("v1.2.4") is True
    assert len(calls) == 2 + release.TAG_POLL_CONSECUTIVE_SUCCESSES


def test_wait_for_tag_gives_up_after_the_poll_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "run_quiet", lambda args, **kwargs: (1, "404"))

    assert release.wait_for_tag("v1.2.4") is False


@pytest.mark.parametrize(
    ("exit_codes", "outputs", "expected_message"),
    [
        pytest.param(
            {"log -1 --format=%H": 1},
            {},
            "`git log -1 --format=%H origin/main -- CHANGELOG.md` failed",
            id="git-read-fails",
        ),
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
        ((1, 4, 2), release.Bump.PATCH, (1, 4, 3)),
        ((1, 4, 2), release.Bump.MINOR, (1, 5, 0)),
        ((1, 4, 2), release.Bump.MAJOR, (2, 0, 0)),
        (None, release.Bump.PATCH, (0, 1, 0)),
        (None, release.Bump.MAJOR, (0, 1, 0)),
    ],
)
def test_next_version(current: release.Version | None, bump: release.Bump, expected: release.Version):
    assert release.next_version(current, bump) == expected


@pytest.mark.parametrize(
    ("version", "bump", "expected"),
    [
        ((1, 4, 2), release.Bump.MINOR, (1, 6, 0)),
        ((1, 4, 2), release.Bump.MAJOR, (3, 0, 0)),
        ((0, 1, 0), release.Bump.MINOR, (0, 3, 0)),
    ],
)
def test_second_next_version(version: release.Version, bump: release.Bump, expected: release.Version):
    assert release.second_next_version(version, bump) == expected


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v1.2.3", (1, 2, 3)), ("v0.0.0", (0, 0, 0)), ("v1.2", None), ("v1.2.3-rc1", None), ("nightly", None)],
)
def test_parse_version(tag: str, expected: release.Version | None):
    assert release.parse_version(tag) == expected


def test_format_version():
    assert release.format_version((2, 0, 5)) == "v2.0.5"
