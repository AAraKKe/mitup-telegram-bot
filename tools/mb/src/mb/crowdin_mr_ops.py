"""Publish pulled Crowdin translations as a merge request from CI.

`mb locales create-mr` is the tail of the scheduled crowdin-pull job: after
`mb locales pull` has refreshed the catalogs, it rebuilds the fixed MR branch from the
checked-out HEAD, commits the catalog changes, and force-pushes with GitLab push options
that create the merge request or update the open one in place. Labeling that MR with
HOLD_LABEL pauses the job before it touches anything.
"""

import os
import subprocess

import httpx

from . import console, runner

MR_BRANCH = "crowdin-translations"
HOLD_LABEL = "crowdin::hold"
MR_TITLE = "🗣️ Update approved translations from Crowdin"
LOCALES_PATHSPEC = "libs/core/mitup_bot/locales/*.po"

# CI_JOB_TOKEN can neither read the merge-requests API nor push, hence the dedicated token.
TOKEN_ENV_VAR = "CROWDIN_GIT_TOKEN"
CI_ENV_VARS = ("CI_API_V4_URL", "CI_PROJECT_ID", "CI_SERVER_HOST", "CI_PROJECT_PATH", "CI_DEFAULT_BRANCH")
REQUEST_TIMEOUT_S = 30.0


def ci_environment() -> dict[str, str] | None:
    values = {name: os.environ.get(name, "") for name in (TOKEN_ENV_VAR, *CI_ENV_VARS)}
    if missing := [name for name, value in values.items() if not value]:
        console.error(f"Missing environment variable(s) {', '.join(missing)} — create-mr runs inside GitLab CI.")
        return None
    return values


def hold_requested(environment: dict[str, str]) -> bool:
    response = httpx.get(
        f"{environment['CI_API_V4_URL']}/projects/{environment['CI_PROJECT_ID']}/merge_requests",
        params={"state": "opened", "source_branch": MR_BRANCH},
        headers={"PRIVATE-TOKEN": environment[TOKEN_ENV_VAR]},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    return any(HOLD_LABEL in merge_request["labels"] for merge_request in response.json())


def token_identity(environment: dict[str, str]) -> tuple[str, str]:
    """The token owner's name and commit email.

    The project enforces the committer-email push rule: a push is rejected unless the
    commit's committer email belongs to the pushing user, so the commit must be authored
    as whoever owns the token.
    """
    response = httpx.get(
        f"{environment['CI_API_V4_URL']}/user",
        headers={"PRIVATE-TOKEN": environment[TOKEN_ENV_VAR]},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    user = response.json()
    return user["name"], user.get("commit_email") or user["email"]


def git(*args: str):
    subprocess.run(["git", *args], cwd=runner.repo_root(), check=True)


def catalogs_changed() -> bool:
    completed = subprocess.run(["git", "diff", "--quiet", "--", LOCALES_PATHSPEC], cwd=runner.repo_root(), check=False)
    return completed.returncode != 0


def push_translations_branch(environment: dict[str, str]):
    committer_name, committer_email = token_identity(environment)
    git("config", "user.name", committer_name)
    git("config", "user.email", committer_email)
    git("checkout", "-B", MR_BRANCH)
    git("add", "--", LOCALES_PATHSPEC)
    git("commit", "-m", MR_TITLE)
    remote = (
        f"https://oauth2:{environment[TOKEN_ENV_VAR]}@{environment['CI_SERVER_HOST']}"
        f"/{environment['CI_PROJECT_PATH']}.git"
    )
    git(
        "push",
        "--force",
        "-o",
        "merge_request.create",
        "-o",
        f"merge_request.target={environment['CI_DEFAULT_BRANCH']}",
        "-o",
        f"merge_request.title={MR_TITLE}",
        remote,
        f"HEAD:{MR_BRANCH}",
    )


def create_translations_mr() -> int:
    environment = ci_environment()
    if environment is None:
        return 1
    try:
        if hold_requested(environment):
            console.warn(f"The open {MR_BRANCH} MR is labeled {HOLD_LABEL} — leaving it untouched.")
            return 0
        if not catalogs_changed():
            console.success("Catalogs already match the approved Crowdin translations.")
            return 0
        push_translations_branch(environment)
    except httpx.HTTPError as error:
        console.error(f"GitLab API error: {error}")
        return 1
    except subprocess.CalledProcessError as error:
        # The failed argv may embed the push token — report only the exit code.
        console.error(f"git failed with exit code {error.returncode}.")
        return 1
    console.success(f"Force-pushed {MR_BRANCH}; the merge request carries the current approved-vs-main delta.")
    return 0
