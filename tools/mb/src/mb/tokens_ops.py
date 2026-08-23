"""Rotate the group's automation tokens and refresh the CI/CD variables that carry them.

Every GitLab call shells out to `glab`, so the command rides the developer's keyring
session and mb never handles an owner credential. Each entry is processed end to end
(rotate, write the variable, read it back) before the next one starts, which keeps the
window where a running CI job could read the revoked value to under a second. After each
write the new value lands on the clipboard for storage in a password manager; it is never
printed, and GitLab itself only ever returns it from the rotate call.
"""

import datetime
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

import typer
from pydantic import BaseModel, TypeAdapter, ValidationError

from . import console, runner

GROUP = "meetupbot"
BOT_PROJECT = "meetupbot/mitup-telegram-bot"
# GitLab's rotate endpoints default the replacement token's expiry to one week; every
# rotation pins an explicit year so a "successful" run cannot schedule a breakage.
TOKEN_LIFETIME_DAYS = 365


class VariableScope(StrEnum):
    PROJECT = "project"
    GROUP = "group"


@dataclass(frozen=True)
class VariableRef:
    scope: VariableScope
    path: str
    key: str

    @property
    def api_path(self) -> str:
        return f"{self.scope.value}s/{quote(self.path, safe='')}/variables/{self.key}"

    def describe(self) -> str:
        return f"{self.key} ({self.scope.value} {self.path})"


@dataclass(frozen=True)
class RotationEntry:
    """One registered token and the CI/CD variable its value feeds.

    *account* names the service account owning the token; None means a group access
    token, which lives directly on the group.
    """

    token_name: str
    variable: VariableRef
    account: str | None = None

    def describe(self) -> str:
        owner = self.account or f"group {GROUP}"
        return f"{self.token_name} ({owner})"


ROTATIONS: tuple[RotationEntry, ...] = (
    RotationEntry(
        "mr-api", VariableRef(VariableScope.PROJECT, BOT_PROJECT, "MITUP_GITLAB_API_TOKEN"), "mitup-gitlab-bot"
    ),
    RotationEntry(
        "mr-push", VariableRef(VariableScope.PROJECT, BOT_PROJECT, "MITUP_GITLAB_PUSH_TOKEN"), "mitup-gitlab-bot"
    ),
    RotationEntry("triage-api", VariableRef(VariableScope.GROUP, GROUP, "GITLAB_API_TOKEN"), "mitup-triage-bot"),
    RotationEntry("Renovate 2026", VariableRef(VariableScope.GROUP, GROUP, "RENOVATE_TOKEN")),
)


class ServiceAccount(BaseModel):
    id: int
    username: str


class AccessToken(BaseModel):
    id: int
    name: str
    active: bool
    expires_at: str | None = None


class RotatedToken(BaseModel):
    token: str


class VariableState(BaseModel):
    value: str


ServiceAccountList = TypeAdapter(list[ServiceAccount])
AccessTokenList = TypeAdapter(list[AccessToken])


class GlabError(RuntimeError): ...


class RotationPlanError(RuntimeError): ...


@dataclass(frozen=True)
class ResolvedRotation:
    entry: RotationEntry
    token: AccessToken
    rotate_path: str


def glab(args: list[str], *, secret: bool = False) -> str:
    """Run `glab api *args* and return its output, raising GlabError on failure.

    *secret* marks calls whose output can carry a token or variable value: their output
    is never echoed, not even on failure.
    """
    exit_code, output = runner.run_quiet(["glab", "api", *args])
    if exit_code == 0:
        return output
    if not secret and output.strip():
        console.raw(output.rstrip())
    raise GlabError(f"`glab api {args[0]}` failed with exit code {exit_code}.")


def service_account_ids() -> dict[str, int]:
    payload = glab([f"groups/{GROUP}/service_accounts"])
    return {account.username: account.id for account in ServiceAccountList.validate_json(payload)}


def find_token(tokens: list[AccessToken], name: str) -> AccessToken | None:
    """The single active token matching *name*, or None when absent or ambiguous.

    Names are compared stripped: GitLab stores them exactly as typed, stray whitespace
    included.
    """
    matches = [token for token in tokens if token.active and token.name.strip() == name]
    return matches[0] if len(matches) == 1 else None


def ensure_variable_exists(variable: VariableRef):
    try:
        glab([variable.api_path], secret=True)
    except GlabError:
        raise RotationPlanError(f"CI/CD variable {variable.describe()} does not exist.") from None


def token_base_path(entry: RotationEntry, accounts: dict[str, int]) -> str:
    if entry.account is None:
        return f"groups/{GROUP}/access_tokens"
    if (account_id := accounts.get(entry.account)) is None:
        raise RotationPlanError(f"Service account {entry.account} not found in group {GROUP}.")
    return f"groups/{GROUP}/service_accounts/{account_id}/personal_access_tokens"


def resolve_rotation(entry: RotationEntry, accounts: dict[str, int]) -> ResolvedRotation:
    base_path = token_base_path(entry, accounts)
    tokens = AccessTokenList.validate_json(glab([base_path]))
    if (token := find_token(tokens, entry.token_name)) is None:
        raise RotationPlanError(f"No single active token named {entry.token_name!r} for {entry.describe()}.")
    ensure_variable_exists(entry.variable)
    return ResolvedRotation(entry, token, f"{base_path}/{token.id}/rotate")


def build_plan() -> list[ResolvedRotation] | None:
    accounts = service_account_ids()
    plan: list[ResolvedRotation] = []
    problems: list[str] = []
    for entry in ROTATIONS:
        try:
            plan.append(resolve_rotation(entry, accounts))
        except RotationPlanError as error:
            problems.append(str(error))
    for problem in problems:
        console.error(problem)
    return None if problems else plan


def print_plan(plan: list[ResolvedRotation], expires_at: str):
    table = console.styled_table("Token rotation plan")
    table.add_column("Token")
    table.add_column("Expires")
    table.add_column("Writes to")
    for resolved in plan:
        table.add_row(resolved.entry.describe(), resolved.token.expires_at or "?", resolved.entry.variable.describe())
    console.show(table)
    console.info(f"Every replacement token will expire on {expires_at}.")


def next_expiry() -> str:
    return (datetime.date.today() + datetime.timedelta(days=TOKEN_LIFETIME_DAYS)).isoformat()


def rotate_token(resolved: ResolvedRotation, expires_at: str) -> str:
    payload = glab([resolved.rotate_path, "-X", "POST", "-f", f"expires_at={expires_at}"], secret=True)
    return RotatedToken.model_validate_json(payload).token


def write_variable(variable: VariableRef, value: str):
    glab([variable.api_path, "-X", "PUT", "-f", f"value={value}"], secret=True)


def verify_variable(variable: VariableRef, expected: str):
    payload = glab([variable.api_path], secret=True)
    if VariableState.model_validate_json(payload).value != expected:
        raise GlabError(f"Variable {variable.describe()} did not read back the rotated value.")


def clipboard_copy(value: str) -> bool:
    if shutil.which("pbcopy") is None:
        return False
    subprocess.run(["pbcopy"], input=value.encode(), check=True)
    return True


def clipboard_clear():
    if shutil.which("pbcopy") is not None:
        subprocess.run(["pbcopy"], input=b"", check=True)


def hand_off_value(value: str):
    if clipboard_copy(value):
        typer.prompt("New value is on the clipboard. Store it, then press Enter", default="", show_default=False)
        return
    console.warn("pbcopy is unavailable: read the value from the CI/CD variable settings instead.")


def execute_rotation(resolved: ResolvedRotation, expires_at: str):
    new_value = rotate_token(resolved, expires_at)
    write_variable(resolved.entry.variable, new_value)
    verify_variable(resolved.entry.variable, new_value)
    console.success(f"{resolved.entry.describe()} rotated; {resolved.entry.variable.describe()} updated.")
    hand_off_value(new_value)


def rotate_tokens(dry_run: bool) -> int:
    expires_at = next_expiry()
    try:
        plan = build_plan()
        if plan is None:
            return 1
        print_plan(plan, expires_at)
        if dry_run:
            console.success("Dry run: nothing was rotated.")
            return 0
        if not typer.confirm(f"Rotate {len(plan)} token(s) and update their variables?"):
            console.warn("Aborted: nothing was rotated.")
            return 1
        for resolved in plan:
            execute_and_report(resolved, expires_at)
    except (GlabError, ValidationError) as error:
        console.error(str(error))
        return 1
    finally:
        clipboard_clear()
    console.success("All tokens rotated.")
    return 0


def execute_and_report(resolved: ResolvedRotation, expires_at: str):
    """Run one rotation, naming the possibly-stale variable before re-raising a failure.

    The rotate call revokes the old value immediately, so a failure after it means the
    variable may hold a dead token until the command is re-run.
    """
    try:
        execute_rotation(resolved, expires_at)
    except GlabError, ValidationError:
        console.error(
            f"Stopped at {resolved.entry.describe()}: its old value is already revoked; "
            f"check {resolved.entry.variable.describe()} and re-run (rotating again is safe)."
        )
        raise
