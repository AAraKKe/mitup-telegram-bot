"""Build a GitLab client from the variables a CI job provides.

CI_JOB_TOKEN can neither read the merge-requests API, post notes, nor push, so CI-run
commands authenticate with a PAT of the Mitup GitLab Bot service account instead.
"""

import os

from . import console, gitlab_client

TOKEN_ENV_VAR = "MITUP_GITLAB_TOKEN"


def ci_environment(required: tuple[str, ...]) -> dict[str, str] | None:
    """Collect the service-account token plus *required* CI variables, or report what is missing."""
    values = {name: os.environ.get(name, "") for name in (TOKEN_ENV_VAR, *required)}
    if missing := [name for name, value in values.items() if not value]:
        console.error(f"Missing environment variable(s) {', '.join(missing)} — this command runs inside GitLab CI.")
        return None
    return values


def api_from(environment: dict[str, str]) -> gitlab_client.GitLabApi:
    return gitlab_client.GitLabApi(environment["CI_API_V4_URL"], environment[TOKEN_ENV_VAR])
