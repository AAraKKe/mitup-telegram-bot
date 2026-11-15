import os
import re
import subprocess
import tomllib
from pathlib import Path

from . import console, crowdin_mr_ops

FRAGMENT_DIRECTORY = "changelog.d"
README_NAME = "README.md"
# `776.fix.md`, `776.fix.1.md` for a second fragment of the same type, or `+slug.fix.md` until
# `mb release prepare` numbers it.
FRAGMENT_NAME = re.compile(r"(?P<number>\d+|\+[\w-]+)\.(?P<type>\w+)(?:\.\d+)?\.md", re.ASCII)
# A fragment may open with comments that explain it, which never reach the changelog.
HTML_COMMENT = re.compile(r"\A(?:\s*<!--.*?-->)+\s*", re.DOTALL)
RELEASE_BRANCH_PREFIX = "release/"
RENOVATE_BRANCH_PREFIX = "renovate/"
# The one merge request pipeline that runs on the source head, not on a commit placing it on top of its target.
DETACHED_PIPELINE_EVENT = "detached"
SKILL_PATH = ".agents/skills/changelog/SKILL.md"
WHATS_NEW_PAGE = "docs/whats-new/index.md"
# The line `mb release prepare` leaves where the release MR writes the summary for users.
RELEASE_SUMMARY_PLACEHOLDER = "<!-- release-summary -->"


def fragment_types(repo_root: Path) -> list[str]:
    """The fragment types declared in the root pyproject's `[tool.towncrier]` table, in section order."""
    towncrier = tomllib.loads((repo_root / "pyproject.toml").read_text())["tool"]["towncrier"]
    return [fragment_type["directory"] for fragment_type in towncrier["type"]]


def fragment_problem(path: Path, types: list[str]) -> str | None:
    if not (match := FRAGMENT_NAME.fullmatch(path.name)):
        return "not named <MR number>.<type>.md, <MR number>.<type>.<counter>.md or +<slug>.<type>.md"
    if match["type"] not in types:
        return f"unknown type {match['type']!r}, expected one of {', '.join(types)}"
    if not HTML_COMMENT.sub("", path.read_text()).strip():
        return "the fragment is empty"
    return None


def added_fragment_names(repo_root: Path) -> list[str]:
    """The fragments the merge request adds to its target, read from the one commit the CI clone holds and the
    commit it fetches to compare with.

    A detached pipeline compares its source head with the fork point; any other one runs on a commit whose first
    parent is the target.
    """
    if os.environ.get("CI_MERGE_REQUEST_EVENT_TYPE") == DETACHED_PIPELINE_EVENT:
        base = os.environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
        missing = ["--depth=1", "origin", base]
    else:
        base, missing = "HEAD^1", ["--depth=2", "origin", os.environ.get("CI_COMMIT_SHA", "")]
    present = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if present.returncode != 0:
        subprocess.run(["git", "fetch", "--quiet", *missing], cwd=repo_root, check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "--diff-filter=A", base, "HEAD", "--", FRAGMENT_DIRECTORY],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line).name for line in completed.stdout.splitlines()]


def adds_unnumbered_internal_fragment(repo_root: Path) -> bool:
    """Whether the branch adds a `+<slug>.internal.md` fragment, the shape tooling writes before the MR exists."""
    try:
        names = added_fragment_names(repo_root)
    except subprocess.CalledProcessError as error:
        console.error(f"Could not list the fragments this merge request adds: {error.stderr.strip()}")
        return False
    if any(
        (match := FRAGMENT_NAME.fullmatch(name)) and match["number"].startswith("+") and match["type"] == "internal"
        for name in names
    ):
        return True
    console.error("This automated merge request adds no unnumbered internal changelog fragment.")
    console.raw(
        f"\nThe tooling that opened it writes {FRAGMENT_DIRECTORY}/+<branch>-<UTC timestamp>.internal.md; "
        f"the `changelog` skill ({SKILL_PATH}) explains how."
    )
    return False


def has_own_fragment(repo_root: Path, fragments: list[Path], types: list[str]) -> bool:
    """Whether the running merge request carries a fragment of its own, or needs none."""
    merge_request_iid = os.environ.get("CI_MERGE_REQUEST_IID", "")
    source_branch = os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "")
    if not merge_request_iid:
        console.info("Not a merge request pipeline: skipping the check for the MR's own fragment.")
        return True
    if source_branch.startswith(RELEASE_BRANCH_PREFIX):
        console.info(f"Release branch {source_branch} is exempt from carrying its own fragment.")
        return True
    if any(
        (match := FRAGMENT_NAME.fullmatch(path.name)) and match["number"] == merge_request_iid for path in fragments
    ):
        return True
    if source_branch.startswith(RENOVATE_BRANCH_PREFIX) or source_branch == crowdin_mr_ops.MR_BRANCH:
        return adds_unnumbered_internal_fragment(repo_root)

    console.error(f"This merge request has no changelog fragment named after it (!{merge_request_iid}).")
    console.raw(
        f"\nCreate {FRAGMENT_DIRECTORY}/{merge_request_iid}.<type>.md, where <type> is one of "
        f"{', '.join(types)}, holding one or two sentences about the change.\n"
        f"The `changelog` skill ({SKILL_PATH}) explains which type to pick and how to word it."
    )
    return False


def has_summary_placeholder(repo_root: Path) -> bool:
    whats_new = repo_root / WHATS_NEW_PAGE
    if not whats_new.is_file() or RELEASE_SUMMARY_PLACEHOLDER not in whats_new.read_text():
        return False
    console.error(
        f"{WHATS_NEW_PAGE} still holds {RELEASE_SUMMARY_PLACEHOLDER}: replace it with the summary for users "
        "before this merges (the `prepare-release` skill has the rules)."
    )
    return True


def run_check(repo_root: Path) -> int:
    """Validate every fragment in changelog.d, require one named after the running merge request, and refuse
    a What's new page that still holds the release summary placeholder."""
    types = fragment_types(repo_root)
    fragments = sorted(path for path in (repo_root / FRAGMENT_DIRECTORY).iterdir() if path.name != README_NAME)
    problems = [(path.name, problem) for path in fragments if (problem := fragment_problem(path, types))]
    for name, problem in problems:
        console.error(f"{FRAGMENT_DIRECTORY}/{name}: {problem}")

    own_fragment = has_own_fragment(repo_root, fragments, types)
    if has_summary_placeholder(repo_root) or not own_fragment or problems:
        return 1
    console.success("Changelog fragments are valid.")
    return 0
