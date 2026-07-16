import re
import time
from typing import Annotated

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import console, runner

DEFAULT_BRANCH = "main"
FIRST_VERSION = (0, 1, 0)
VERSION_TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")
MERGE_REQUEST_REF_RE = re.compile(r"!(\d+)")
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
RELEASE_POST_ATTEMPTS = 12

Version = tuple[int, int, int]


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


def latest_version() -> Version | None:
    """Highest `vMAJOR.MINOR.PATCH` tag, or None when no version tag exists yet."""
    versions = [parsed for line in git_capture("tag", "--list", "v*").splitlines() if (parsed := parse_version(line))]
    return max(versions) if versions else None


def next_version(current: Version | None, bump: str) -> Version:
    if current is None:
        return FIRST_VERSION
    major, minor, _ = current
    if bump == "major":
        return (major + 1, 0, 0)
    return (major, minor + 1, 0)


def second_next_version(version: Version, bump: str) -> Version:
    """Version two releases ahead of *version* in the bumped dimension — the runway milestone to open.

    The very next release always has a milestone already, so opening the one two ahead keeps a
    spare bucket for issues at all times (releasing v0.2.0 as a minor opens v0.4.0; a major opens
    two majors out).
    """
    major, minor, _ = version
    if bump == "major":
        return (major + 2, 0, 0)
    return (major, minor + 2, 0)


def format_version(version: Version) -> str:
    major, minor, patch = version
    return f"v{major}.{minor}.{patch}"


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
    """MR IIDs referenced by the commits shipping in this release, newest first, without duplicates.

    Squash and merge commits carry their MR as a `!123` reference, so the range `previous_tag..sha`
    (all of `sha`'s history when there is no previous tag) yields exactly what changed since the
    last release.
    """
    revision_range = f"{previous_tag}..{sha}" if previous_tag else sha
    log = git_capture("log", "--format=%B", revision_range)
    ids: list[int] = []
    for match in MERGE_REQUEST_REF_RE.finditer(log):
        iid = int(match[1])
        if iid not in ids:
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


def shipped_merge_requests(previous_tag: str | None, sha: str) -> list[MergeRequest]:
    """The merge requests shipping in this release, newest first, dropping any that cannot be fetched.

    Fetched once and shared by the release notes and the milestone sweep, so each MR costs a single
    API call.
    """
    return [mr for iid in merge_request_ids(previous_tag, sha) if (mr := merge_request(iid))]


def build_release_notes(merge_requests: list[MergeRequest]) -> str:
    """Changelog body listing every shipped merge request as `[!IID] title`."""
    return "\n".join(f"- [!{mr.iid}] {mr.title}" for mr in merge_requests)


def milestone_id(title: str) -> int | None:
    """Numeric id of the project milestone titled *title*, or None when it cannot be resolved."""
    exit_code, output = runner.run_quiet(["glab", "api", f"projects/:id/milestones?title={title}"])
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
    milestone can always be set by hand from the release's changelog list.
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
    """Whether a project milestone titled *title* already exists (False when the query cannot answer)."""
    exit_code, output = runner.run_quiet(["glab", "api", f"projects/:id/milestones?title={title}"])
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
    the POST is retried until the tag registers. The retry budget spans about two minutes
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


def release_command(
    minor: Annotated[bool, typer.Option("--minor", help="Bump the minor version (the default).")] = False,
    major: Annotated[bool, typer.Option("--major", help="Bump the major version instead of the minor.")] = False,
):
    """Tag origin/main's green tip as a `v*` release, publish it with notes, open the runway
    milestone, and milestone the shipped merge requests.

    Reads nothing from the local checkout: the release always ships the freshest origin/main
    commit, so a dirty tree, untracked files, or a feature branch never block or influence it.
    """
    if minor and major:
        console.error("Pass at most one of --minor/--major.")
        raise typer.Abort()

    fetch_origin()
    sha = git_capture("rev-parse", f"origin/{DEFAULT_BRANCH}")
    ensure_pipeline_is_green(sha)

    bump = "major" if major else "minor"
    previous = latest_version()
    previous_tag = format_version(previous) if previous else None
    released = next_version(previous, bump)
    version = format_version(released)

    create_and_push_tag(version, sha)

    ensure_milestone(format_version(second_next_version(released, bump)))
    milestone = version if ensure_milestone(version) else None
    with console.spinner("Waiting for the tag to register"):
        tag_visible = wait_for_tag(version)
    if not tag_visible:
        console.warn(f"Tag {version} is not visible via the API yet; the release may fail to attach.")
    shipped = shipped_merge_requests(previous_tag, sha)
    create_release(version, build_release_notes(shipped), milestone)
    if milestone is not None:
        assign_merge_requests_to_milestone(shipped, milestone)

    console.success(f"Released {version}")
    with console.spinner("Waiting for the deploy pipeline to start"):
        pipeline_url = pipeline_url_for_ref(version)
    if pipeline_url:
        console.info(f"Deploy pipeline started: {pipeline_url}")
    else:
        console.warn("The deploy pipeline has not appeared yet; give it a moment.")
        console.info(f"Watch for it here: {PIPELINES_URL}?ref={version}")
