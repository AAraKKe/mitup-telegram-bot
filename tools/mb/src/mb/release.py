import re
import sys
import tempfile
import time
from datetime import date
from enum import IntEnum
from itertools import chain, count
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import console, runner
from .changelog_check import (
    FRAGMENT_DIRECTORY,
    FRAGMENT_NAME,
    HTML_COMMENT,
    RELEASE_SUMMARY_PLACEHOLDER,
    WHATS_NEW_PAGE,
)

DEFAULT_BRANCH = "main"
FIRST_VERSION = (0, 1, 0)
VERSION_TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")
MERGE_REQUEST_REF_RE = re.compile(r"!(\d+)")
# The suffix GitLab's squash template gives the subject of every commit a merge request lands on main.
MERGE_REQUEST_SUFFIX_RE = re.compile(r"\(!(\d+)\)$")
COMMIT_SEPARATOR = "\x00"
CHANGELOG = "CHANGELOG.md"
CHANGELOG_VERSION_HEADING = re.compile(r"^## (v\d+\.\d+\.\d+)\b", re.MULTILINE)
CHANGELOG_TYPE_HEADING = re.compile(r"^### (.+)$", re.MULTILINE)
RELEASES_MARKER = re.compile(r"^<!-- releases -->$", re.MULTILINE)
RELEASE_BRANCH = "release/{version}"
RELEASE_TITLE = "⚙️ Release {version}"
RELEASE_MR_DESCRIPTION = """\
Compiles the pending changelog fragments into `CHANGELOG.md` and the What's new page for {version}.

Left to do, in this order:

1. Write the summary for users in `{page}`, in place of the `{placeholder}` line. The `prepare-release` skill \
has the rules. The `check-changelog` job fails until the placeholder is gone.
2. Merge this merge request.
3. Run `uv run mb release` to tag the commit it lands as {version}.
"""
PIPELINES_URL = "https://gitlab.com/meetupbot/mitup-telegram-bot/-/pipelines"
PIPELINE_POLL_INTERVAL_SECONDS = 3
PIPELINE_POLL_ATTEMPTS = 20
TAG_POLL_INTERVAL_SECONDS = 2
TAG_POLL_ATTEMPTS = 30
# One successful read proves nothing on gitlab.com: tag lookups are served by replicated Gitaly
# nodes, so a fresh node can answer 200 while stale ones still miss the tag. Requiring several
# consecutive hits is the same convergence heuristic the pipeline reads use.
TAG_POLL_CONSECUTIVE_SUCCESSES = 3
RELEASE_POST_INTERVAL_SECONDS = 10
# The release POST needs a long runway: GitLab re-checks protected-tag *creation* permission even
# when the tag already exists (gitlab-org/gitlab#448356), so any stale replica turns into a 403
# rather than a retryable miss, and convergence on gitlab.com has been observed to exceed two
# minutes after the tag polls already read consistently. Six minutes of retries absorbs that.
RELEASE_POST_ATTEMPTS = 36

Version = tuple[int, int, int]


class Bump(IntEnum):
    PATCH = 1
    MINOR = 2
    MAJOR = 3


FRAGMENT_BUMPS = {
    "feature": Bump.MAJOR,
    "removal": Bump.MAJOR,
    "change": Bump.MINOR,
    "fix": Bump.PATCH,
    "internal": Bump.PATCH,
}


class Pipeline(BaseModel):
    id: int
    status: str
    web_url: str | None = None


PipelineList = TypeAdapter(list[Pipeline])


class Milestone(BaseModel):
    title: str
    id: int | None = None


class MergeRequest(BaseModel):
    iid: int
    title: str
    milestone: Milestone | None = None


MilestoneList = TypeAdapter(list[Milestone])

app = typer.Typer()


def git_capture(*args: str) -> str:
    """Run a read-only git command, abort on failure, and return its trimmed stdout."""
    exit_code, output = runner.run_quiet(["git", *args])
    if exit_code != 0:
        console.error(f"`git {' '.join(args)}` failed:")
        console.raw(output.rstrip())
        raise typer.Abort()
    return output.strip()


def fetch_origin():
    if runner.run_step("Fetching origin", ["git", "fetch", "--tags", "origin"]) != 0:
        raise typer.Abort()


def ensure_pipeline_is_green(sha: str):
    """Abort unless the latest GitLab pipeline for *sha* finished successfully."""
    exit_code, output = runner.run_quiet(["glab", "api", f"projects/:id/pipelines?sha={sha}"])
    if exit_code != 0:
        console.error("Could not query the GitLab pipeline status via `glab`:")
        console.raw(output.rstrip())
        raise typer.Abort()

    try:
        pipelines = PipelineList.validate_json(output)
    except ValidationError:
        console.error("Could not parse the GitLab pipeline response:")
        console.raw(output.rstrip())
        raise typer.Abort() from None

    if not pipelines:
        console.error(f"No pipeline found for commit {sha[:8]} on GitLab; wait for CI to run before releasing.")
        raise typer.Abort()

    latest = max(pipelines, key=lambda pipeline: pipeline.id)
    if latest.status != "success":
        console.error(
            f"The pipeline for commit {sha[:8]} is {latest.status!r}, not 'success'; "
            "a release must ship a green commit."
        )
        raise typer.Abort()


def pipeline_url_for_ref(ref: str) -> str | None:
    """Poll GitLab until a pipeline for *ref* exists, returning its web URL (None if none starts in time).

    Called after the tag is already pushed, so a query failure or timeout is non-fatal: the release
    stands regardless, and the caller falls back to the ref-filtered pipelines listing.
    """
    for attempt in range(PIPELINE_POLL_ATTEMPTS):
        exit_code, output = runner.run_quiet(["glab", "api", f"projects/:id/pipelines?ref={ref}"])
        if exit_code == 0:
            try:
                pipelines = PipelineList.validate_json(output)
            except ValidationError:
                pipelines = []
            if pipelines:
                return max(pipelines, key=lambda pipeline: pipeline.id).web_url
        if attempt < PIPELINE_POLL_ATTEMPTS - 1:
            time.sleep(PIPELINE_POLL_INTERVAL_SECONDS)
    return None


def parse_version(tag: str) -> Version | None:
    if (match := VERSION_TAG_RE.fullmatch(tag)) is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def remote_versions() -> list[Version]:
    """Every `vMAJOR.MINOR.PATCH` tag on origin.

    Reads the remote tag listing, never the local one: a local-only tag would count as a release
    that origin never saw. The preceding fetch guarantees every remote tag also resolves locally
    for the `git log` range of shipped merge requests.
    """
    versions: list[Version] = []
    for line in git_capture("ls-remote", "--tags", "origin", "v*").splitlines():
        tag = line.split("refs/tags/")[-1]
        if tag.endswith("^{}"):
            continue
        if parsed := parse_version(tag):
            versions.append(parsed)
    return versions


def next_version(current: Version | None, bump: Bump) -> Version:
    if current is None:
        return FIRST_VERSION
    major, minor, patch = current
    if bump == Bump.MAJOR:
        return (major + 1, 0, 0)
    if bump == Bump.MINOR:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def second_next_version(version: Version, bump: Bump) -> Version:
    """Version two releases ahead of *version* in the bumped dimension: the runway milestone to open.

    The very next release always has a milestone already, so opening the one two ahead keeps a
    spare bucket for issues at all times (releasing v0.2.0 as a minor opens v0.4.0; a major opens
    two majors out).
    """
    major, minor, _ = version
    if bump == Bump.MAJOR:
        return (major + 2, 0, 0)
    return (major, minor + 2, 0)


def format_version(version: Version) -> str:
    major, minor, patch = version
    return f"v{major}.{minor}.{patch}"


def prepared_version(changelog: str, versions: list[Version]) -> Version | None:
    """The newest version in CHANGELOG.md when origin has no tag for it yet."""
    if (match := CHANGELOG_VERSION_HEADING.search(changelog)) and (version := parse_version(match[1])) not in versions:
        return version
    return None


def changelog_section(changelog: str, version: str) -> str:
    """The body of *version*'s section in CHANGELOG.md, without its heading."""
    sections = re.split(r"^## ", changelog, flags=re.MULTILINE)
    section = next((section for section in sections if section.startswith(f"{version} ")), "")
    return section.partition("\n")[2].strip()


def whats_new_section(version: str, day: date, changelog_body: str) -> str:
    """The What's new section for *version*: the summary placeholder above the changelog folded into a note,
    whose headings become bold labels so the page's table of contents keeps one entry per release."""
    labelled = CHANGELOG_TYPE_HEADING.sub(r"**\1**", changelog_body)
    folded = "\n".join(f"    {line}" if line else "" for line in labelled.splitlines())
    return (
        f"## {version}\n\n_{day.day} {day:%B %Y}_\n\n{RELEASE_SUMMARY_PLACEHOLDER}\n\n"
        f'??? note "Full changelog"\n\n{folded}'
    )


def insert_release_section(page: str, section: str) -> str:
    """*page* with *section* inserted right after its releases marker line, so the newest release comes first."""
    marker = RELEASES_MARKER.search(page)
    assert marker is not None, "prepare checks the marker before it builds anything"
    rest = page[marker.end() :].lstrip("\n")
    return f"{page[: marker.end()]}\n\n{section}\n\n{rest}".rstrip("\n") + "\n"


def whats_new_page(ref: str) -> str:
    exit_code, page = runner.run_quiet(["git", "show", f"{ref}:{WHATS_NEW_PAGE}"])
    if exit_code != 0:
        console.error(f"Commit {ref[:8]} has no {WHATS_NEW_PAGE}.")
        raise typer.Abort()
    return page


def pending_fragments(sha: str) -> dict[str, list[str]]:
    """The changelog fragment file names at *sha*, grouped by type in changelog order."""
    fragments: dict[str, list[str]] = {fragment_type: [] for fragment_type in FRAGMENT_BUMPS}
    for path in git_capture("ls-tree", "--name-only", sha, f"{FRAGMENT_DIRECTORY}/").splitlines():
        if (match := FRAGMENT_NAME.fullmatch(Path(path).name)) and match["type"] in fragments:
            fragments[match["type"]].append(Path(path).name)
    return fragments


def numbered_names(sha: str, fragments: dict[str, list[str]]) -> dict[str, str]:
    """The name each unnumbered fragment at *sha* takes in the release, numbered after the merge request that
    first added it so its changelog line links there.

    The number ends the subject of the oldest commit adding the file, and a name already taken gets a counter.
    """
    taken = {name for names in fragments.values() for name in names}
    renames: dict[str, str] = {}
    for kind, names in fragments.items():
        for name in names:
            if not name.startswith("+"):
                continue
            path = f"{FRAGMENT_DIRECTORY}/{name}"
            subjects = git_capture("log", "--diff-filter=A", "--format=%s", sha, "--", path).splitlines()
            subject = subjects[-1] if subjects else ""
            if (match := MERGE_REQUEST_SUFFIX_RE.search(subject)) is None:
                console.error(f"Cannot tell which merge request added {path}.")
                console.raw(f"The commit that added it must have a subject ending in `(!123)`; found {subject!r}.")
                console.raw(f"Rename it to <MR number>.{kind}.md in a merge request, then prepare the release again.")
                raise typer.Abort()
            candidates = chain([f"{match[1]}.{kind}.md"], (f"{match[1]}.{kind}.{counter}.md" for counter in count(1)))
            renames[name] = next(candidate for candidate in candidates if candidate not in taken)
            taken.add(renames[name])
    return renames


def print_plan(
    sha: str,
    previous: Version | None,
    fragments: dict[str, list[str]],
    renames: dict[str, str],
    computed: Bump,
    chosen: Bump,
):
    version = format_version(next_version(previous, chosen))
    console.info(f"Previous version: {format_version(previous) if previous else 'none'}")
    console.info(f"Version to prepare: {version} ({chosen.name.lower()})")
    console.info("Pending fragments: " + ", ".join(f"{len(names)} {kind}" for kind, names in fragments.items()))
    if chosen != computed:
        computed_version = format_version(next_version(previous, computed))
        console.info(
            f"The fragments call for {computed_version} ({computed.name.lower()}); the flag chooses {version}."
        )
    console.info(f"Fragments that make it a {computed.name.lower()} release:")
    deciding = [name for kind, names in fragments.items() if FRAGMENT_BUMPS[kind] == computed for name in names]
    for name in deciding:
        console.raw(f"  {name}: {HTML_COMMENT.sub('', git_capture('show', f'{sha}:{FRAGMENT_DIRECTORY}/{name}'))}")
    if renames:
        console.info("Fragments written before their merge request existed take its number:")
        for name, numbered in renames.items():
            console.raw(f"  {name} -> {numbered}")


def commit_release(checkout: Path, version: str, page: str, renames: dict[str, str]):
    """Number the unnumbered fragments, drop the comment a fragment opens with, compile CHANGELOG.md and the
    What's new section for *version* in *checkout*, and commit them.

    The commit skips the git hooks: they run `uv run --no-sync --frozen mb`, which has no environment in a
    fresh checkout, and the release MR's pipeline runs the same checks.
    """
    for name, numbered in renames.items():
        move = ["git", "mv", f"{FRAGMENT_DIRECTORY}/{name}", f"{FRAGMENT_DIRECTORY}/{numbered}"]
        if runner.run_step(f"Numbering {name} as {numbered}", move, cwd=checkout) != 0:
            raise typer.Abort()
    for fragment in (checkout / FRAGMENT_DIRECTORY).iterdir():
        if FRAGMENT_NAME.fullmatch(fragment.name):
            fragment.write_text(HTML_COMMENT.sub("", fragment.read_text()))
    today = date.today()
    towncrier = [sys.executable, "-m", "towncrier", "build", "--version", version, "--date", today.isoformat(), "--yes"]
    if runner.run_step("Compiling the changelog", towncrier, cwd=checkout) != 0:
        raise typer.Abort()
    section = whats_new_section(version, today, changelog_section((checkout / CHANGELOG).read_text(), version))
    (checkout / WHATS_NEW_PAGE).write_text(insert_release_section(page, section))
    commit = ["git", "commit", "--no-verify", "--message", RELEASE_TITLE.format(version=version)]
    for description, args in [("Staging the What's new page", ["git", "add", WHATS_NEW_PAGE]), ("Committing", commit)]:
        if runner.run_step(description, args, cwd=checkout) != 0:
            raise typer.Abort()


def push_release_branch(sha: str, version: str, page: str, renames: dict[str, str]):
    """Commit the release on top of *sha* in a temporary worktree and push it as the release branch."""
    branch = RELEASE_BRANCH.format(version=version)
    with tempfile.TemporaryDirectory(prefix="mb-release-") as scratch:
        checkout = Path(scratch) / "checkout"
        add_worktree = ["git", "worktree", "add", "--detach", str(checkout), sha]
        if runner.run_step(f"Checking out {sha[:8]}", add_worktree) != 0:
            raise typer.Abort()
        try:
            commit_release(checkout, version, page, renames)
            push = ["git", "push", "--no-verify", "origin", f"HEAD:refs/heads/{branch}"]
            if runner.run_step(f"Pushing {branch}", push, cwd=checkout) != 0:
                raise typer.Abort()
        finally:
            runner.run_quiet(["git", "worktree", "remove", "--force", str(checkout)])


def open_release_merge_request(version: str):
    description = RELEASE_MR_DESCRIPTION.format(
        version=version, page=WHATS_NEW_PAGE, placeholder=RELEASE_SUMMARY_PLACEHOLDER
    )
    exit_code, output = runner.run_quiet(
        [
            "glab",
            "mr",
            "create",
            "--source-branch",
            RELEASE_BRANCH.format(version=version),
            "--target-branch",
            DEFAULT_BRANCH,
            "--title",
            RELEASE_TITLE.format(version=version),
            "--description",
            description,
            "--remove-source-branch",
            "--yes",
        ]
    )
    if exit_code != 0:
        console.error("Could not open the release merge request via `glab`:")
        console.raw(output.rstrip())
        raise typer.Abort()
    console.success(f"Opened the release merge request for {version}")
    console.raw(output.strip())


@app.command("prepare")
def prepare_command(
    patch: Annotated[bool, typer.Option("--patch", help="Prepare a patch release whatever the fragments say.")] = False,
    minor: Annotated[bool, typer.Option("--minor", help="Prepare a minor release whatever the fragments say.")] = False,
    major: Annotated[bool, typer.Option("--major", help="Prepare a major release whatever the fragments say.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation after the plan.")] = False,
):
    """Open the release merge request that compiles the pending changelog fragments on origin/main.

    The version follows the highest fragment type unless a flag overrides it. Nothing is created before the
    plan is confirmed, and the local checkout is never read.
    """
    if sum([patch, minor, major]) > 1:
        console.error("Pass at most one of --patch/--minor/--major.")
        raise typer.Abort()

    fetch_origin()
    sha = git_capture("rev-parse", f"origin/{DEFAULT_BRANCH}")
    fragments = pending_fragments(sha)
    computed = max((FRAGMENT_BUMPS[kind] for kind, names in fragments.items() if names), default=None)
    if computed is None:
        console.error(f"origin/{DEFAULT_BRANCH} has no changelog fragments: there is nothing to release.")
        raise typer.Abort()

    versions = remote_versions()
    if prepared := prepared_version(git_capture("show", f"{sha}:{CHANGELOG}"), versions):
        console.error(f"{format_version(prepared)} is prepared but not tagged: run `uv run mb release` first.")
        raise typer.Abort()
    page = whats_new_page(sha)
    if not RELEASES_MARKER.search(page):
        console.error(f"{WHATS_NEW_PAGE} has no `<!-- releases -->` line to insert the release after.")
        raise typer.Abort()

    renames = numbered_names(sha, fragments)
    chosen = Bump.MAJOR if major else Bump.MINOR if minor else Bump.PATCH if patch else computed
    previous = max(versions, default=None)
    print_plan(sha, previous, fragments, renames, computed, chosen)
    version = format_version(next_version(previous, chosen))
    if not yes:
        typer.confirm(f"Prepare {version}?", abort=True)

    push_release_branch(sha, version, page, renames)
    open_release_merge_request(version)


def create_and_push_tag(version: str, sha: str):
    if runner.run_step(f"Creating tag {version}", ["git", "tag", "-a", version, sha, "-m", version]) != 0:
        raise typer.Abort()
    if runner.run_step(f"Pushing tag {version}", ["git", "push", "origin", version]) != 0:
        raise typer.Abort()


def wait_for_tag(version: str) -> bool:
    """Poll until the pushed tag reads back consistently, returning whether it converged in time.

    `git push` and the API are eventually consistent. Cutting the release before the API sees the
    tag makes GitLab's release service treat it as missing and refuse with a protected-tag 403 (the
    v0.4.0 release hit exactly this even after a single successful tag read). One 200 only proves
    one replica has the tag, so the poll demands `TAG_POLL_CONSECUTIVE_SUCCESSES` hits in a row —
    a failed read resets the streak — before the release POST is attempted.
    """
    consecutive_successes = 0
    for attempt in range(TAG_POLL_ATTEMPTS):
        exit_code, _ = runner.run_quiet(["glab", "api", f"projects/:id/repository/tags/{version}"])
        consecutive_successes = consecutive_successes + 1 if exit_code == 0 else 0
        if consecutive_successes >= TAG_POLL_CONSECUTIVE_SUCCESSES:
            return True
        if attempt < TAG_POLL_ATTEMPTS - 1:
            time.sleep(TAG_POLL_INTERVAL_SECONDS)
    return False


def merge_request_ids(previous_tag: str | None, sha: str) -> list[int]:
    """MR IIDs of the commits shipping in this release, newest first, without duplicates.

    A commit ships exactly one merge request, the one it was merged from, named by the first `!123`
    reference in its message. A feature-branch squash embeds the messages of the commits it
    squashed, each naming the MR that landed it on the feature branch, and those never count.
    """
    revision_range = f"{previous_tag}..{sha}" if previous_tag else sha
    log = git_capture("log", "--format=%B%x00", revision_range)
    ids: list[int] = []
    for message in log.split(COMMIT_SEPARATOR):
        match = MERGE_REQUEST_REF_RE.search(message)
        if match and (iid := int(match[1])) not in ids:
            ids.append(iid)
    return ids


def merge_request(iid: int) -> MergeRequest | None:
    """Merge request *iid* via the GitLab API, or None when the lookup fails."""
    exit_code, output = runner.run_quiet(["glab", "api", f"projects/:id/merge_requests/{iid}"])
    if exit_code != 0:
        return None
    try:
        return MergeRequest.model_validate_json(output)
    except ValidationError:
        return None


def milestone_id(title: str) -> int | None:
    """Numeric id of the project or ancestor-group milestone titled *title*, or None when it cannot be resolved."""
    exit_code, output = runner.run_quiet(
        ["glab", "api", f"projects/:id/milestones?title={title}&include_parent_milestones=true"]
    )
    if exit_code != 0:
        return None
    try:
        milestones = MilestoneList.validate_json(output)
    except ValidationError:
        return None
    return milestones[0].id if milestones else None


def assign_merge_requests_to_milestone(merge_requests: list[MergeRequest], title: str):
    """Set milestone *title* on every shipped merge request that has none yet.

    An MR that already carries a milestone was assigned deliberately (e.g. to a later version), so
    it is left untouched. Runs after the release is published, so every failure is non-fatal: the
    milestone can always be set by hand from the release's merge requests.
    """
    unassigned: list[MergeRequest] = []
    for mr in merge_requests:
        if mr.milestone is None:
            unassigned.append(mr)
        else:
            console.info(f"!{mr.iid} already belongs to {mr.milestone.title}; leaving it")
    if not unassigned:
        return

    resolved_id = milestone_id(title)
    if resolved_id is None:
        console.warn(f"Could not resolve the id of milestone {title}; assign the shipped MRs manually.")
        return

    for mr in unassigned:
        exit_code, _ = runner.run_quiet(
            ["glab", "api", "-X", "PUT", f"projects/:id/merge_requests/{mr.iid}", "-f", f"milestone_id={resolved_id}"]
        )
        if exit_code == 0:
            console.info(f"Assigned {title} to !{mr.iid} {mr.title}")
        else:
            console.warn(f"Could not assign {title} to !{mr.iid}; set it manually.")


def milestone_exists(title: str) -> bool:
    """Whether a milestone titled *title* already exists (False when the query cannot answer).

    Ancestor-group milestones count: GitLab rejects a project milestone whose title shadows a
    group one, so an existing group milestone must be reused rather than re-created. Releases and
    merge requests accept group milestones exactly like project ones.
    """
    exit_code, output = runner.run_quiet(
        ["glab", "api", f"projects/:id/milestones?title={title}&include_parent_milestones=true"]
    )
    if exit_code != 0:
        return False
    try:
        return bool(MilestoneList.validate_json(output))
    except ValidationError:
        return False


def ensure_milestone(title: str) -> bool:
    """Open the project milestone *title* if it is missing; return whether it exists afterward.

    A creation failure is non-fatal: it returns False so the caller can carry on without the
    milestone rather than let a broken `glab` invocation sink the release.
    """
    if milestone_exists(title):
        console.info(f"Milestone {title} already exists")
        return True
    if runner.run_step(f"Opening milestone {title}", ["glab", "milestone", "create", "--title", title]) != 0:
        console.warn(f"Could not open milestone {title}; create it manually if needed.")
        return False
    return True


def create_release(version: str, notes: str, milestone: str | None):
    """Publish a GitLab release for *version*, titled with the tag and milestoned when *milestone* is set.

    The release is POSTed to the REST API directly with only `tag_name` — never a ref — so GitLab
    can do nothing but attach it to the already-pushed tag. `glab release create` instead validates
    the tag with its own API read first, and GitLab's replicas are eventually consistent: when that
    read misses the fresh tag, glab falls back to creating the tag from a ref, which the protected
    `v*` pattern rejects with a 403. Without a ref, a stale replica merely fails the request, and
    the POST is retried until the tag registers. The retry budget spans about six minutes
    (`RELEASE_POST_ATTEMPTS` x `RELEASE_POST_INTERVAL_SECONDS`) because replica convergence has
    been observed to outlast a short burst of retries even after the tag polls read consistently.

    Runs after the tag is pushed, so exhausting the retries is non-fatal: the tag and its deploy
    pipeline already stand, and the release can be cut by hand from the existing tag.
    """
    args = [
        "glab",
        "api",
        "-X",
        "POST",
        "projects/:id/releases",
        "-f",
        f"tag_name={version}",
        "-f",
        f"name={version}",
        "-f",
        f"description={notes}",
    ]
    if milestone is not None:
        args += ["-f", f"milestones[]={milestone}"]

    output = ""
    for attempt in range(RELEASE_POST_ATTEMPTS):
        exit_code, output = runner.run_quiet(args)
        if exit_code == 0:
            console.info(f"Created release {version}")
            return
        if attempt < RELEASE_POST_ATTEMPTS - 1:
            time.sleep(RELEASE_POST_INTERVAL_SECONDS)
    console.warn(f"Could not create the GitLab release for {version}; the tag is pushed, so create it manually.")
    console.raw(output.rstrip())


def release_commit() -> str:
    """The last commit on origin/main that changed CHANGELOG.md, which is where the release MR landed."""
    sha = git_capture("log", "-1", "--format=%H", f"origin/{DEFAULT_BRANCH}", "--", CHANGELOG)
    if later := int(git_capture("rev-list", "--count", f"{sha}..origin/{DEFAULT_BRANCH}")):
        console.info(f"origin/{DEFAULT_BRANCH} has {later} commits after {sha[:8]}; they ship in the next release.")
    return sha


def ensure_whats_new_is_ready(sha: str, version: str):
    page = whats_new_page(sha)
    if not re.search(rf"^## {re.escape(version)}$", page, re.MULTILINE):
        console.error(f"{WHATS_NEW_PAGE} at {sha[:8]} has no section for {version}.")
        raise typer.Abort()
    if RELEASE_SUMMARY_PLACEHOLDER in page:
        console.error(f"{WHATS_NEW_PAGE} at {sha[:8]} still holds {RELEASE_SUMMARY_PLACEHOLDER}: write the summary.")
        raise typer.Abort()


def publish_release(released: Version, previous: Version | None, sha: str, notes: str):
    """Publish the GitLab release for the pushed tag; minor and major releases also open the runway milestone
    and milestone the shipped merge requests."""
    version = format_version(released)
    major, minor, _ = previous or (0, 0, 0)
    bump = Bump.MAJOR if released[0] != major else Bump.MINOR if released[1] != minor else Bump.PATCH
    milestone = None
    if bump != Bump.PATCH:
        ensure_milestone(format_version(second_next_version(released, bump)))
        milestone = version if ensure_milestone(version) else None
    with console.spinner("Waiting for the tag to register"):
        tag_visible = wait_for_tag(version)
    if not tag_visible:
        console.warn(f"Tag {version} is not visible via the API yet; the release may fail to attach.")
    create_release(version, notes, milestone)
    if milestone is not None:
        previous_tag = format_version(previous) if previous else None
        shipped = [mr for iid in merge_request_ids(previous_tag, sha) if (mr := merge_request(iid))]
        assign_merge_requests_to_milestone(shipped, milestone)


@app.callback(invoke_without_command=True)
def release_command(context: typer.Context):
    """Tag the release prepared on origin/main and publish it; `mb release prepare` opens the release MR first.

    The version is the newest one in CHANGELOG.md and the tagged commit is the last one that changed that
    file. The local checkout is never read.
    """
    if context.invoked_subcommand is not None:
        return

    fetch_origin()
    versions = remote_versions()
    changelog = git_capture("show", f"origin/{DEFAULT_BRANCH}:{CHANGELOG}")
    if (released := prepared_version(changelog, versions)) is None:
        console.error(
            f"Nothing is prepared: every version in {CHANGELOG} on origin/{DEFAULT_BRANCH} is already tagged. "
            "Run `uv run mb release prepare` first."
        )
        raise typer.Abort()
    version = format_version(released)
    sha = release_commit()
    ensure_whats_new_is_ready(sha, version)
    ensure_pipeline_is_green(sha)
    create_and_push_tag(version, sha)
    publish_release(released, max(versions, default=None), sha, changelog_section(changelog, version))

    console.success(f"Released {version}")
    with console.spinner("Waiting for the deploy pipeline to start"):
        pipeline_url = pipeline_url_for_ref(version)
    if pipeline_url:
        console.info(f"Deploy pipeline started: {pipeline_url}")
    else:
        console.warn("The deploy pipeline has not appeared yet; give it a moment.")
        console.info(f"Watch for it here: {PIPELINES_URL}?ref={version}")
