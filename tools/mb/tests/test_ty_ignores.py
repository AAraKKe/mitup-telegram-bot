from pathlib import Path

import pytest
from mb import ty_ignores

# The directive text is assembled at runtime: the CI scan reads tools/ sources as raw
# text (strings included), so a literal directive here would register as a real suppression.
DIRECTIVE = "# ty: " + "ignore"
TRACKED_LINE = f"x = 1  {DIRECTIVE}[missing-argument]  https://github.com/astral-sh/ty/issues/123\n"
UNTRACKED_LINE = f"y = 2  {DIRECTIVE}[invalid-return-type]\n"
NOLINK_LINE = f"z = 3  {DIRECTIVE}[arg-type]  # nolink: intentional mismatch in fixture\n"


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    (tmp_path / "mitup_bot").mkdir()
    (tmp_path / "mitup_bot" / "tracked.py").write_text(TRACKED_LINE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "untracked.py").write_text(UNTRACKED_LINE)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "exempted.py").write_text(NOLINK_LINE)
    return tmp_path


def test_scan_collects_tracked_and_untracked_but_skips_nolink(source_tree: Path):
    tracked, untracked = ty_ignores.scan_ty_ignores(source_tree)

    assert [entry.rule for entry in tracked] == ["missing-argument"]
    assert tracked[0].issue.number == 123
    assert tracked[0].issue.url == "https://github.com/astral-sh/ty/issues/123"
    assert [entry.rule for entry in untracked] == ["invalid-return-type"]


def test_untracked_suppressions_fail_the_report():
    report = ty_ignores.CheckReport(untracked=[ty_ignores.UntrackedIgnore(file="a.py", line=1, rule="arg-type")])

    assert report.has_failures
    assert "UNTRACKED" in str(report)


def test_closed_issues_fail_the_report_and_list_removable_entries():
    issue = ty_ignores.GitHubIssueRef(owner="astral-sh", repo="ty", number=9, url="https://github.com/x/y/issues/9")
    entry = ty_ignores.IgnoreEntry(file="a.py", line=3, rule="arg-type", issue=issue)
    report = ty_ignores.CheckReport(
        entries=[entry],
        issue_states={issue.url: ty_ignores.IssueCheckResult(issue=issue, state=ty_ignores.IssueState.CLOSED)},
    )

    assert report.has_failures
    assert report.resolved_entries == [entry]
    assert "ACTION REQUIRED" in str(report)


def test_open_issues_pass_the_report():
    issue = ty_ignores.GitHubIssueRef(owner="astral-sh", repo="ty", number=9, url="https://github.com/x/y/issues/9")
    entry = ty_ignores.IgnoreEntry(file="a.py", line=3, rule="arg-type", issue=issue)
    report = ty_ignores.CheckReport(
        entries=[entry],
        issue_states={issue.url: ty_ignores.IssueCheckResult(issue=issue, state=ty_ignores.IssueState.OPEN)},
    )

    assert not report.has_failures


def test_run_check_passes_on_a_tree_without_suppressions(tmp_path: Path):
    assert ty_ignores.run_check(tmp_path) == 0
