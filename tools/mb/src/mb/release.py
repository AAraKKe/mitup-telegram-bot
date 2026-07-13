import re
import time
from typing import Annotated

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import console, runner

DEFAULT_BRANCH = "main"
FIRST_VERSION = (0, 1, 0)
VERSION_TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")
PIPELINES_URL = "https://gitlab.com/meetupbot/mitup-telegram-bot/-/pipelines"
PIPELINE_POLL_INTERVAL_SECONDS = 3
PIPELINE_POLL_ATTEMPTS = 20

Version = tuple[int, int, int]


class Pipeline(BaseModel):
    id: int
    status: str
    web_url: str | None = None


PipelineList = TypeAdapter(list[Pipeline])


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
    major, minor, patch = current
    match bump:
        case "major":
            return (major + 1, 0, 0)
        case "minor":
            return (major, minor + 1, 0)
        case _:
            return (major, minor, patch + 1)


def format_version(version: Version) -> str:
    major, minor, patch = version
    return f"v{major}.{minor}.{patch}"


def create_and_push_tag(version: str, sha: str):
    if runner.run_step(f"Creating tag {version}", ["git", "tag", "-a", version, sha, "-m", version]) != 0:
        raise typer.Abort()
    if runner.run_step(f"Pushing tag {version}", ["git", "push", "origin", version]) != 0:
        raise typer.Abort()


def release_command(
    minor: Annotated[bool, typer.Option("--minor", help="Bump the minor version instead of the patch.")] = False,
    major: Annotated[bool, typer.Option("--major", help="Bump the major version instead of the patch.")] = False,
):
    """Tag origin/main's green tip as a `v*` release and push it to trigger the deploy pipeline.

    Reads nothing from the local checkout: the release always ships the freshest origin/main
    commit, so a dirty tree, untracked files, or a feature branch never block or influence it.
    """
    if minor and major:
        console.error("Pass at most one of --minor/--major.")
        raise typer.Abort()

    fetch_origin()
    sha = git_capture("rev-parse", f"origin/{DEFAULT_BRANCH}")
    ensure_pipeline_is_green(sha)

    bump = "major" if major else "minor" if minor else "patch"
    version = format_version(next_version(latest_version(), bump))

    create_and_push_tag(version, sha)

    console.success(f"Released {version}")
    with console.spinner("Waiting for the deploy pipeline to start"):
        pipeline_url = pipeline_url_for_ref(version)
    if pipeline_url:
        console.info(f"Deploy pipeline started: {pipeline_url}")
    else:
        console.warn("The deploy pipeline has not appeared yet; give it a moment.")
        console.info(f"Watch for it here: {PIPELINES_URL}?ref={version}")
