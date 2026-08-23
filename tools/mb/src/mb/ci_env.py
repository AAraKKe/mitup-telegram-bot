"""Build a GitLab client from the variables a CI job provides.

CI_JOB_TOKEN can neither read the merge-requests API, post notes, nor push, so CI-run
commands authenticate with PATs of the Mitup GitLab Bot service account instead, one per
capability: an api-scope token for REST calls and a write_repository-scope token for git
pushes. Both PATs belong to the same account, so the API token's owner is also the
identity every pushed commit must carry to pass the committer-email push rule.
"""

import os

from . import console, gitlab_client

API_TOKEN_ENV_VAR = "MITUP_GITLAB_API_TOKEN"
PUSH_TOKEN_ENV_VAR = "MITUP_GITLAB_PUSH_TOKEN"


def ci_environment(required: tuple[str, ...]) -> dict[str, str] | None:
    """Collect the service-account API token plus *required* CI variables, or report what is missing."""
    values = {name: os.environ.get(name, "") for name in (API_TOKEN_ENV_VAR, *required)}
    if missing := [name for name, value in values.items() if not value]:
        console.error(f"Missing environment variable(s) {', '.join(missing)} — this command runs inside GitLab CI.")
        return None
    return values


def api_from(environment: dict[str, str]) -> gitlab_client.GitLabApi:
    return gitlab_client.GitLabApi(environment["CI_API_V4_URL"], environment[API_TOKEN_ENV_VAR])
