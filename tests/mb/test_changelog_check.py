from pathlib import Path

import pytest

from mb import changelog_check, console, crowdin_mr_ops

MERGE_REQUEST_IID = "790"
FEATURE_BRANCH = "380-changelog-fragments"
FRAGMENT_TYPES = ("feature", "removal", "change", "fix", "internal")


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
@pytest.mark.parametrize(
    ("name", "text", "reason"),
    [
        ("790.bug.md", "Text.", "unknown type 'bug'"),
        ("790-fix.md", "Text.", "not named"),
        ("790.fix.txt", "Text.", "not named"),
        ("fix.md", "Text.", "not named"),
        ("791.change.md", " \n", "the fragment is empty"),
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


@pytest.mark.parametrize("source_branch", ["renovate/ruff-0.x", crowdin_mr_ops.MR_BRANCH])
def test_automated_branches_need_their_own_fragment_too(
    repo_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, source_branch: str
):
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", MERGE_REQUEST_IID)
    monkeypatch.setenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", source_branch)

    assert run_check(repo_root, capsys)[0] == 1


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
