"""Post the uv.lock package delta of a merge request as a single self-maintaining comment.

`mb ci comment-lock-diff` runs on MR pipelines whose diff touches uv.lock. It compares the
checked-out lock against the MR's diff base, renders the added/removed/changed packages —
flagging which ones are declared dependencies and which are transitive — and posts the result
as an MR note. The note carries MARKER, and later runs update that note in place, so a
rebuilt MR (e.g. a Renovate force-push) refreshes the comment instead of stacking a new one.
"""

import re
import subprocess
import tomllib
from pathlib import Path

import httpx

from . import ci_env, console, gitlab_client, runner

MARKER = "<!-- mb:lock-diff -->"
CI_ENV_VARS = ("CI_API_V4_URL", "CI_PROJECT_ID", "CI_MERGE_REQUEST_IID", "CI_MERGE_REQUEST_DIFF_BASE_SHA")
DEPENDENCY_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def canonical_name(name: str) -> str:
    """PEP 503 normalization, so declared names match uv.lock's package names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_versions(lock_toml: str) -> dict[str, str]:
    packages = tomllib.loads(lock_toml).get("package", [])
    return {entry["name"]: entry["version"] for entry in packages if "version" in entry}


def workspace_pyprojects(root: Path) -> list[Path]:
    """The root pyproject plus every member's, resolved from `[tool.uv.workspace].members`."""
    root_pyproject = root / "pyproject.toml"
    workspace = tomllib.loads(root_pyproject.read_text()).get("tool", {}).get("uv", {}).get("workspace", {})
    members = (path for pattern in workspace.get("members", []) for path in root.glob(pattern))
    return [root_pyproject, *(member / "pyproject.toml" for member in members if (member / "pyproject.toml").is_file())]


def declared_dependencies(root: Path) -> set[str]:
    """Canonical names declared by any workspace member — everything else in the lock is transitive."""
    declared: set[str] = set()
    for pyproject in workspace_pyprojects(root):
        data = tomllib.loads(pyproject.read_text())
        project = data.get("project", {})
        requirement_lists = [
            project.get("dependencies", []),
            *project.get("optional-dependencies", {}).values(),
            *data.get("dependency-groups", {}).values(),
        ]
        for requirement in (item for group in requirement_lists for item in group if isinstance(item, str)):
            if match := DEPENDENCY_NAME_RE.match(requirement):
                declared.add(canonical_name(match.group(1)))
    return declared


def base_lock_content(base_sha: str, root: Path) -> str | None:
    """The uv.lock content at the MR's diff base, fetched by sha to survive shallow clones."""
    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", base_sha],
        cwd=root,
        check=True,
    )
    completed = subprocess.run(
        ["git", "show", f"{base_sha}:uv.lock"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout if completed.returncode == 0 else None


def diff_lines(old: dict[str, str], new: dict[str, str], declared: set[str]) -> list[str]:
    lines = []
    for name in sorted(old.keys() | new.keys()):
        old_version, new_version = old.get(name), new.get(name)
        if old_version == new_version:
            continue
        origin = "direct" if canonical_name(name) in declared else "transitive"
        match old_version, new_version:
            case None, _:
                lines.append(f"| `{name}` | — | {new_version} | added | {origin} |")
            case _, None:
                lines.append(f"| `{name}` | {old_version} | — | removed | {origin} |")
            case _:
                lines.append(f"| `{name}` | {old_version} | {new_version} | changed | {origin} |")
    return lines


def render_comment(lines: list[str]) -> str:
    if not lines:
        return f"{MARKER}\n### 🔒 uv.lock changes\n\nNo package changes in `uv.lock` for the current revision."
    header = (
        f"{MARKER}\n### 🔒 uv.lock changes ({len(lines)} package{'s' if len(lines) != 1 else ''})\n\n"
        "| Package | Old | New | Change | Origin |\n|---|---|---|---|---|\n"
    )
    return header + "\n".join(lines)


def comment_lock_diff() -> int:
    environment = ci_env.ci_environment(CI_ENV_VARS)
    if environment is None:
        return 1
    root = runner.repo_root()
    try:
        base_content = base_lock_content(environment["CI_MERGE_REQUEST_DIFF_BASE_SHA"], root)
        old_versions = package_versions(base_content) if base_content is not None else {}
        new_versions = package_versions((root / "uv.lock").read_text())
        lines = diff_lines(old_versions, new_versions, declared_dependencies(root))
        ci_env.api_from(environment).upsert_merge_request_note(
            environment["CI_PROJECT_ID"], environment["CI_MERGE_REQUEST_IID"], MARKER, render_comment(lines)
        )
    except subprocess.CalledProcessError as error:
        console.error(f"git failed with exit code {error.returncode}.")
        return 1
    except httpx.HTTPError as error:
        console.error(f"GitLab API error: {gitlab_client.describe_error(error)}")
        return 1
    console.success(f"Lock-diff comment reflects {len(lines)} changed package(s).")
    return 0
