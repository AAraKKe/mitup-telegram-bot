import os
import subprocess
from pathlib import Path

import pytest

from mb import changelog_check, console, crowdin_mr_ops

MERGE_REQUEST_IID = "790"
FEATURE_BRANCH = "380-changelog-fragments"
FRAGMENT_TYPES = ("feature", "removal", "change", "fix", "internal")
RENOVATE_BRANCH = "renovate/python-dependencies"
RENOVATE_FRAGMENT = "+renovate-python-dependencies-20261114090000.internal.md"


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch):
    # Pin a wide console so long messages are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    towncrier_types = "".join(f'[[tool.towncrier.type]]\ndirectory = "{name}"\n\n' for name in FRAGMENT_TYPES)
    (tmp_path / "pyproject.toml").write_text(f'[tool.towncrier]\ndirectory = "changelog.d"\n\n{towncrier_types}')
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("Changelog fragments.\n")
    return tmp_path


@pytest.fixture
def merge_request(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", MERGE_REQUEST_IID)
    monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", FEATURE_BRANCH)


def write_fragments(repo_root: Path, *names: str, text: str = "Something users can see changed.\n"):
    for name in names:
        (repo_root / "changelog.d" / name).write_text(text)


def run_check(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    exit_code = changelog_check.run_check(repo_root)
    captured = capsys.readouterr()
    return exit_code, captured.out + captured.err


@pytest.mark.usefixtures("merge_request")
def test_passes_with_own_fragment_in_every_valid_name_form(repo_root: Path, capsys: pytest.CaptureFixture[str]):
    write_fragments(repo_root, "790.fix.md", "790.fix.1.md", "+tidy-ci.internal.md", "12.feature.md")

    exit_code, output = run_check(repo_root, capsys)

    assert exit_code == 0
    assert "Changelog fragments are valid" in output


@pytest.mark.usefixtures("merge_request")
def test_passes_with_a_fragment_that_explains_itself_in_a_comment(repo_root: Path, capsys: pytest.CaptureFixture[str]):
    write_fragments(repo_root, "790.fix.md")
    write_fragments(repo_root, "+crowdin-translations.internal.md", text=crowdin_mr_ops.FRAGMENT_TEXT)

    assert run_check(repo_root, capsys)[0] == 0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("<!-- Why. -->\nSentence.\n", "Sentence.\n", id="opening"),
        pytest.param("\n  <!-- Why,\nat length. -->\nSentence.\n", "Sentence.\n", id="opening-after-blank-lines"),
        pytest.param("Sentence.\n<!-- Why. -->\nMore.\n", "Sentence.\n<!-- Why. -->\nMore.\n", id="mid-text"),
    ],
)
def test_html_comment_matches_only_the_opening_comment(text: str, expected: str):
    assert changelog_check.HTML_COMMENT.sub("", text) == expected


@pytest.mark.usefixtures("merge_request")
@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("790.bug.md", "Text.", "unknown type 'bug'"),
        ("790-fix.md", "Text.", "not named"),
        ("790.fix.txt", "Text.", "not named"),
        ("fix.md", "Text.", "not named"),
        ("791.change.md", " \n", "the fragment is empty"),
        ("+crowdin-translations.internal.md", "<!-- Why the name has no number. -->\n", "the fragment is empty"),
    ],
)
def test_fails_on_an_invalid_fragment_naming_file_and_reason(
    repo_root: Path, capsys: pytest.CaptureFixture[str], name: str, text: str, reason: str
):
    write_fragments(repo_root, "790.fix.md")
    write_fragments(repo_root, name, text=text)

    exit_code, output = run_check(repo_root, capsys)

    assert exit_code == 1
    assert f"changelog.d/{name}: {reason}" in output


@pytest.mark.usefixtures("merge_request")
def test_fails_without_own_fragment_and_names_the_file_to_create(repo_root: Path, capsys: pytest.CaptureFixture[str]):
    write_fragments(repo_root, "12.fix.md")

    exit_code, output = run_check(repo_root, capsys)

    assert exit_code == 1
    assert "Create changelog.d/790.<type>.md" in output
    assert "`changelog` skill" in output


def test_release_branch_needs_no_own_fragment_but_names_are_still_checked(
    repo_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", MERGE_REQUEST_IID)
    monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "release/v3.0.0")

    assert run_check(repo_root, capsys)[0] == 0

    write_fragments(repo_root, "12.bug.md")
    assert run_check(repo_root, capsys)[0] == 1


def test_outside_a_merge_request_only_names_are_checked(
    repo_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("CI_MERGE_REQUEST_IID", raising=False)

    exit_code, output = run_check(repo_root, capsys)

    assert exit_code == 0
    assert "Not a merge request pipeline" in output


@pytest.mark.usefixtures("merge_request")
def test_fails_while_the_whats_new_page_holds_the_summary_placeholder(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
):
    write_fragments(repo_root, "790.fix.md")
    whats_new = repo_root / changelog_check.WHATS_NEW_PAGE
    whats_new.parent.mkdir(parents=True)
    whats_new.write_text(f"## v3.0.0\n\n{changelog_check.RELEASE_SUMMARY_PLACEHOLDER}\n")

    exit_code, output = run_check(repo_root, capsys)

    assert exit_code == 1
    assert f"{changelog_check.WHATS_NEW_PAGE} still holds {changelog_check.RELEASE_SUMMARY_PLACEHOLDER}" in output


# --- automated merge requests ---

CROWDIN_FRAGMENT = "+crowdin-translations-20261114090000.internal.md"
EARLIER_FRAGMENT = "+renovate-lock-file-maintenance-20261113090000.internal.md"
AUTOMATED_BRANCHES = [
    pytest.param(RENOVATE_BRANCH, RENOVATE_FRAGMENT, id="renovate"),
    pytest.param(crowdin_mr_ops.MR_BRANCH, CROWDIN_FRAGMENT, id="crowdin"),
]
MERGED_EVENTS = ["merged_result", "merge_train"]
EVENTS = [*MERGED_EVENTS, "detached"]
MISSING_COMMIT = "0" * 40


def git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def commit_fragments(repo_root: Path, *names: str, text: str = "Dependencies are updated: ruff to 0.17.0.\n") -> str:
    write_fragments(repo_root, *names, text=text)
    git(repo_root, "add", "--all")
    git(repo_root, "commit", "--quiet", "--allow-empty", "--message", "Add fragments")
    return git(repo_root, "rev-parse", "HEAD")


@pytest.fixture
def origin(repo_root: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare `origin` holding `main`, pushed from a work repository at *repo_root* isolated from the developer's
    git configuration, and the environment of a Renovate merge request pipeline."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Renovate Bot")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "bot@renovate.example")
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", MERGE_REQUEST_IID)
    monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", RENOVATE_BRANCH)
    for name in ("CI_MERGE_REQUEST_EVENT_TYPE", "CI_COMMIT_SHA", "CI_MERGE_REQUEST_DIFF_BASE_SHA"):
        monkeypatch.delenv(name, raising=False)
    bare = tmp_path_factory.mktemp("origin")
    git(bare, "init", "--quiet", "--bare")
    git(repo_root, "init", "--quiet", "--initial-branch", "main")
    git(repo_root, "remote", "add", "origin", bare.as_uri())
    commit_fragments(repo_root)
    return bare


@pytest.fixture
def pipeline_checkout(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pipeline")


def fork_source_branch(repo_root: Path) -> str:
    git(repo_root, "switch", "--quiet", "--create", "source")
    return git(repo_root, "rev-parse", "HEAD")


def pipeline_commit(repo_root: Path, monkeypatch: pytest.MonkeyPatch, event_type: str, fork_point: str) -> str:
    """Build the commit a pipeline of *event_type* runs on for the `source` branch, and set its environment."""
    match event_type:
        case "merged_result":
            git(repo_root, "switch", "--quiet", "--detach", "main")
            git(repo_root, "merge", "--quiet", "--no-ff", "--no-edit", "source")
        case "merge_train":
            git(repo_root, "switch", "--quiet", "--detach", "main")
            git(repo_root, "merge", "--quiet", "--squash", "source")
            git(repo_root, "commit", "--quiet", "--allow-empty", "--message", f"Squash source (!{MERGE_REQUEST_IID})")
        case _:
            git(repo_root, "switch", "--quiet", "--detach", "source")
            monkeypatch.setenv("CI_MERGE_REQUEST_DIFF_BASE_SHA", fork_point)
    monkeypatch.setenv("CI_MERGE_REQUEST_EVENT_TYPE", event_type)
    commit = git(repo_root, "rev-parse", "HEAD")
    monkeypatch.setenv("CI_COMMIT_SHA", commit)
    return commit


def check_out_pipeline_commit(repo_root: Path, checkout: Path, commit: str, *, shallow: bool = True) -> Path:
    """Fetch *commit* through origin into *checkout* the way CI does, holding only that commit when *shallow*."""
    git(repo_root, "push", "--quiet", "origin", f"{commit}:refs/pipelines/{commit}")
    git(checkout, "init", "--quiet")
    git(checkout, "remote", "add", "origin", git(repo_root, "remote", "get-url", "origin"))
    git(checkout, "fetch", "--quiet", *(["--depth=1"] if shallow else []), "origin", commit)
    git(checkout, "switch", "--quiet", "--detach", "FETCH_HEAD")
    return checkout


@pytest.mark.usefixtures("origin")
@pytest.mark.parametrize("event_type", EVENTS)
@pytest.mark.parametrize(("source_branch", "fragment"), AUTOMATED_BRANCHES)
def test_automated_branch_passes_with_an_unnumbered_internal_fragment_it_adds(
    repo_root: Path,
    pipeline_checkout: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    source_branch: str,
    fragment: str,
):
    monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", source_branch)
    commit_fragments(repo_root, EARLIER_FRAGMENT)
    fork_point = fork_source_branch(repo_root)
    commit_fragments(repo_root, fragment)
    commit_fragments(repo_root)
    commit = pipeline_commit(repo_root, monkeypatch, event_type, fork_point)

    exit_code, output = run_check(check_out_pipeline_commit(repo_root, pipeline_checkout, commit), capsys)

    assert exit_code == 0, output


@pytest.mark.usefixtures("origin")
@pytest.mark.parametrize("event_type", EVENTS)
@pytest.mark.parametrize(
    ("added_fragments", "changed_fragments"),
    [
        pytest.param((), (), id="adds-nothing"),
        pytest.param(("+renovate-python-dependencies-20261114090000.fix.md",), (), id="adds-another-type"),
        pytest.param(("12.internal.md",), (), id="adds-a-numbered-fragment"),
        pytest.param((), (EARLIER_FRAGMENT,), id="modifies-the-target-fragment"),
    ],
)
def test_automated_branch_fails_without_an_unnumbered_internal_fragment_of_its_own(
    repo_root: Path,
    pipeline_checkout: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    added_fragments: tuple[str, ...],
    changed_fragments: tuple[str, ...],
):
    commit_fragments(repo_root, EARLIER_FRAGMENT)
    fork_point = fork_source_branch(repo_root)
    commit_fragments(repo_root, *added_fragments)
    commit_fragments(repo_root, *changed_fragments, text="Dependencies are updated: ruff to 0.18.0.\n")
    commit = pipeline_commit(repo_root, monkeypatch, event_type, fork_point)

    exit_code, output = run_check(check_out_pipeline_commit(repo_root, pipeline_checkout, commit), capsys)

    assert exit_code == 1
    assert "adds no unnumbered internal changelog fragment" in output


@pytest.mark.usefixtures("origin")
@pytest.mark.parametrize("event_type", MERGED_EVENTS)
@pytest.mark.parametrize("announced_event_type", ["as-built", "unset", "unknown"])
def test_merged_pipeline_ignores_a_fragment_the_target_gained_after_the_fork(
    repo_root: Path,
    pipeline_checkout: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    announced_event_type: str,
):
    fork_point = fork_source_branch(repo_root)
    commit_fragments(repo_root)
    git(repo_root, "switch", "--quiet", "main")
    commit_fragments(repo_root, EARLIER_FRAGMENT)
    monkeypatch.setenv("CI_MERGE_REQUEST_DIFF_BASE_SHA", fork_point)
    commit = pipeline_commit(repo_root, monkeypatch, event_type, fork_point)
    if announced_event_type == "unset":
        monkeypatch.delenv("CI_MERGE_REQUEST_EVENT_TYPE")
    elif announced_event_type == "unknown":
        monkeypatch.setenv("CI_MERGE_REQUEST_EVENT_TYPE", "a_pipeline_type_gitlab_adds_later")

    exit_code, output = run_check(check_out_pipeline_commit(repo_root, pipeline_checkout, commit), capsys)

    assert exit_code == 1
    assert "adds no unnumbered internal changelog fragment" in output


@pytest.mark.usefixtures("origin")
@pytest.mark.parametrize(
    ("event_type", "missing_variable"),
    [("merged_result", "CI_COMMIT_SHA"), ("detached", "CI_MERGE_REQUEST_DIFF_BASE_SHA")],
)
def test_automated_branch_fails_when_the_commit_to_compare_with_cannot_be_fetched(
    repo_root: Path,
    pipeline_checkout: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    missing_variable: str,
):
    fork_point = fork_source_branch(repo_root)
    commit_fragments(repo_root, RENOVATE_FRAGMENT)
    commit = pipeline_commit(repo_root, monkeypatch, event_type, fork_point)
    checkout = check_out_pipeline_commit(repo_root, pipeline_checkout, commit)
    monkeypatch.setenv(missing_variable, MISSING_COMMIT)

    exit_code, output = run_check(checkout, capsys)

    assert exit_code == 1
    assert "Could not list the fragments this merge request adds:" in output


@pytest.mark.usefixtures("origin")
@pytest.mark.parametrize("event_type", EVENTS)
def test_full_clone_stays_complete(
    repo_root: Path,
    pipeline_checkout: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
):
    fork_point = fork_source_branch(repo_root)
    commit_fragments(repo_root, RENOVATE_FRAGMENT)
    commit = pipeline_commit(repo_root, monkeypatch, event_type, fork_point)
    checkout = check_out_pipeline_commit(repo_root, pipeline_checkout, commit, shallow=False)

    exit_code, output = run_check(checkout, capsys)

    assert exit_code == 0, output
    assert git(checkout, "rev-parse", "--is-shallow-repository") == "false"


@pytest.mark.usefixtures("origin")
def test_automated_branch_passes_with_a_fragment_numbered_after_it(repo_root: Path, capsys: pytest.CaptureFixture[str]):
    write_fragments(repo_root, "790.internal.md")  # named after the merge request, so git is never consulted

    assert run_check(repo_root, capsys)[0] == 0
