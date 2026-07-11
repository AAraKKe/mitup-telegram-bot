"""Scan the codebase for `# ty: ignore` comments that reference GitHub issues,
and check whether those issues have been closed upstream.

When a referenced issue is closed, the suppression is likely no longer needed
and can be removed after verifying the type checker no longer reports the error.
All unique issues are checked concurrently via asyncio TaskGroup.
"""

import asyncio
import json
import re
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from itertools import groupby
from operator import attrgetter
from pathlib import Path

from . import runner

# Directories to scan (relative to repo root)
SCAN_DIRS = ("mitup_bot", "tests", "tools")

# Matches any ty ignore directive (with or without an issue URL).
TY_IGNORE_BARE = re.compile(r"#\s*ty:\s*ignore\[(?P<rule>[^\]]+)\]")

# Matches ty ignore directives that include a GitHub issue URL.
TY_IGNORE_WITH_ISSUE = re.compile(
    r"#\s*ty:\s*ignore\[(?P<rule>[^\]]+)\]"  # the ignore directive + rule
    r".*?"
    r"(?P<url>https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+))",
)

# Matches ty ignore directives that are explicitly exempted from requiring an issue URL.
# Use `# nolink: <reason>` to document why no tracking issue exists (e.g. intentional
# type mismatch in a test fixture rather than a ty bug).
TY_IGNORE_NOLINK = re.compile(r"#\s*ty:\s*ignore\[(?P<rule>[^\]]+)\].*#\s*nolink:")


class IssueState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class GitHubIssueRef:
    """A reference to a GitHub issue, parsed from a URL."""

    owner: str
    repo: str
    number: int
    url: str

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.repo}/issues/{self.number}"


@dataclass(frozen=True, slots=True)
class UntrackedIgnore:
    """A ty: ignore suppression without an associated tracking issue."""

    file: str
    line: int
    rule: str


@dataclass(frozen=True, slots=True)
class IgnoreEntry:
    """A single ty: ignore suppression found in the codebase."""

    file: str
    line: int
    rule: str
    issue: GitHubIssueRef


@dataclass(frozen=True, slots=True)
class IssueCheckResult:
    """The result of checking a single GitHub issue."""

    issue: GitHubIssueRef
    state: IssueState
    error_detail: str | None = None


@dataclass
class CheckReport:
    """Aggregated results of checking all ty: ignore entries."""

    entries: list[IgnoreEntry] = field(default_factory=list)
    untracked: list[UntrackedIgnore] = field(default_factory=list)
    issue_states: dict[str, IssueCheckResult] = field(default_factory=dict)

    def state_for(self, entry: IgnoreEntry) -> IssueState:
        result = self.issue_states.get(entry.issue.url)
        return result.state if result else IssueState.UNKNOWN

    @cached_property
    def entries_by_issue(self) -> dict[str, list[IgnoreEntry]]:
        """Group entries by issue URL. Computed once and cached."""
        key = attrgetter("issue.url")
        return {url: list(group) for url, group in groupby(sorted(self.entries, key=key), key=key)}

    @property
    def has_resolved(self) -> bool:
        return any(r.state == IssueState.CLOSED for r in self.issue_states.values())

    @property
    def has_failures(self) -> bool:
        return self.has_resolved or bool(self.untracked)

    @property
    def resolved_entries(self) -> list[IgnoreEntry]:
        return [
            entry
            for url, entries in self.entries_by_issue.items()
            if self.issue_states.get(url) and self.issue_states[url].state == IssueState.CLOSED
            for entry in entries
        ]

    def _report_lines(self) -> Iterator[str]:
        """Yield lines for the human-readable report."""
        total = len(self.entries) + len(self.untracked)
        yield f"Found {total} ty: ignore suppression(s) across {len(self.issue_states)} unique tracked issue(s).\n"

        if self.untracked:
            yield "UNTRACKED — the following suppressions are missing a GitHub issue URL:\n"
            for entry in self.untracked:
                yield f"  {entry.file}:{entry.line}  [{entry.rule}]"
            yield ""
            yield "Each suppression must reference a tracking issue so it can be removed once fixed."
            yield ""

        for url, entries in self.entries_by_issue.items():
            check_result = self.issue_states.get(url)
            state = check_result.state if check_result else IssueState.UNKNOWN

            match state:
                case IssueState.ERROR:
                    detail = check_result.error_detail if check_result else "unknown"
                    status_label = f"error: {detail}"
                case _:
                    status_label = state.value

            yield f"- {url}  [status: {status_label}]"
            for entry in entries:
                yield f"    {entry.file}:{entry.line}  [{entry.rule}]"
            yield ""

        if resolved := self.resolved_entries:
            yield "─" * 60
            yield ""
            yield "ACTION REQUIRED — the following suppressions can be removed:\n"
            for entry in resolved:
                yield f"  {entry.file}:{entry.line}"
            yield ""
            yield (
                "hint: Run `uv run mb typecheck` after removing these comments "
                "to verify the issues are actually fixed in your version of ty."
            )

    def __str__(self) -> str:
        return "\n".join(self._report_lines())


def scan_ty_ignores(root: Path) -> tuple[list[IgnoreEntry], list[UntrackedIgnore]]:
    """Walk the source tree and collect all ty: ignore entries, tracked and untracked."""
    tracked: list[IgnoreEntry] = []
    untracked: list[UntrackedIgnore] = []

    for scan_dir in SCAN_DIRS:
        target = root / scan_dir
        if not target.is_dir():
            continue
        for py_file in sorted(target.rglob("*.py")):
            for line_no, line in enumerate(py_file.read_text().splitlines(), start=1):
                if match := TY_IGNORE_WITH_ISSUE.search(line):
                    tracked.append(
                        IgnoreEntry(
                            file=str(py_file.relative_to(root)),
                            line=line_no,
                            rule=match.group("rule"),
                            issue=GitHubIssueRef(
                                owner=match.group("owner"),
                                repo=match.group("repo"),
                                number=int(match.group("number")),
                                url=match.group("url"),
                            ),
                        )
                    )
                elif TY_IGNORE_NOLINK.search(line):
                    ...  # explicitly exempted — nolink: reason documents why no issue is needed
                elif bare_match := TY_IGNORE_BARE.search(line):
                    untracked.append(
                        UntrackedIgnore(
                            file=str(py_file.relative_to(root)),
                            line=line_no,
                            rule=bare_match.group("rule"),
                        )
                    )
    return tracked, untracked


async def fetch_issue_state(issue: GitHubIssueRef) -> IssueCheckResult:
    """Query the GitHub API for the state of a single issue."""
    request = urllib.request.Request(
        issue.api_url,
        headers={"Accept": "application/vnd.github.v3+json"},
    )

    def do_request() -> IssueCheckResult:
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read())
                raw_state = data.get("state", "unknown")
                state = IssueState(raw_state) if raw_state in IssueState.__members__.values() else IssueState.UNKNOWN
                return IssueCheckResult(issue=issue, state=state)
        except Exception as exc:
            return IssueCheckResult(issue=issue, state=IssueState.ERROR, error_detail=str(exc))

    return await asyncio.to_thread(do_request)


async def check_all_issues(unique_issues: dict[str, GitHubIssueRef]) -> dict[str, IssueCheckResult]:
    """Check all unique issues concurrently using a TaskGroup."""
    async with asyncio.TaskGroup() as tg:
        tasks = {url: tg.create_task(fetch_issue_state(issue)) for url, issue in unique_issues.items()}

    return {url: task.result() for url, task in tasks.items()}


async def build_report(root: Path) -> CheckReport:
    report = CheckReport()
    report.entries, report.untracked = scan_ty_ignores(root)

    if not report.entries:
        return report

    # Deduplicate: one API call per unique issue URL
    unique_issues = {entry.issue.url: entry.issue for entry in report.entries}

    report.issue_states = await check_all_issues(unique_issues)
    return report


def run_check(root: Path) -> int:
    report = asyncio.run(build_report(root))
    runner.console.print(str(report), markup=False, highlight=False)
    return 1 if report.has_failures else 0
