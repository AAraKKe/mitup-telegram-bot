"""Post the uv.lock package delta of a merge request as a single self-maintaining comment.

`mb ci comment-lock-diff` runs on MR pipelines whose diff touches uv.lock. It compares the
checked-out lock against the MR's diff base and posts the added, removed and changed packages
as an MR note: a table flagging declared and transitive packages and naming where each one comes
from, plus a Mermaid graph of those paths. The note carries MARKER, and later runs update that
note in place, so a rebuilt MR (e.g. a Renovate force-push) refreshes the comment instead of
stacking a new one.
"""

import re
import subprocess
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from . import ci_env, console, gitlab_client, runner

MARKER = "<!-- mb:lock-diff -->"
CI_ENV_VARS = ("CI_API_V4_URL", "CI_PROJECT_ID", "CI_MERGE_REQUEST_IID", "CI_MERGE_REQUEST_DIFF_BASE_SHA")
DEPENDENCY_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
COLLAPSE_GRAPH_ABOVE_LINKS = 40
MAX_VIA_SHOWN = 3
MISSING = "-"
GRAPH_STYLES = (
    "classDef root fill:#dbeafe,stroke:#1d4ed8,color:#1e293b",
    "classDef between fill:#f1f5f9,stroke:#64748b,color:#1e293b",
    "classDef added fill:#bbf7d0,stroke:#15803d,color:#1e293b",
    "classDef removed fill:#fecaca,stroke:#b91c1c,color:#1e293b",
    "classDef changed fill:#fde68a,stroke:#b45309,color:#1e293b",
)

Link = tuple[str, str]


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True)
class LockGraph:
    """Which packages and dependency groups require each package of a uv.lock."""

    requirers: dict[str, set[str]]
    workspace_members: set[str]

    def links_to(self, package: str) -> frozenset[Link]:
        """The (requirer, required) links from our own packages and groups down to `package`."""
        links: set[Link] = set()
        seen = {package}
        pending = [package]
        while pending:
            current = pending.pop()
            requirers = set() if current in self.workspace_members else self.requirers.get(current, set())
            links.update((requirer, current) for requirer in requirers)
            pending.extend(requirers - seen)
            seen |= requirers
        return frozenset(links)


@dataclass(frozen=True)
class PackageChange:
    name: str
    old_version: str | None
    new_version: str | None
    links: frozenset[Link]

    @property
    def kind(self) -> ChangeKind:
        if self.old_version is None:
            return ChangeKind.ADDED
        return ChangeKind.REMOVED if self.new_version is None else ChangeKind.CHANGED

    @property
    def graph_label(self) -> str:
        versions = " → ".join(filter(None, (self.old_version, self.new_version)))
        suffix = "" if self.kind is ChangeKind.CHANGED else f" ({self.kind})"
        return f"{self.name} {versions}{suffix}"


def canonical_name(name: str) -> str:
    """PEP 503 normalization, so declared names match uv.lock's package names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def package_versions(lock_toml: str) -> dict[str, str]:
    packages = tomllib.loads(lock_toml).get("package", [])
    return {entry["name"]: entry["version"] for entry in packages if "version" in entry}


def package_requirements(package: dict[str, Any]) -> list[str]:
    requirement_lists = [
        package.get("dependencies", []),
        *package.get("optional-dependencies", {}).values(),
        *package.get("dev-dependencies", {}).values(),
    ]
    return [entry["name"] for entries in requirement_lists for entry in entries]


def lock_graph(lock_toml: str) -> LockGraph:
    lock = tomllib.loads(lock_toml)
    manifest = lock.get("manifest", {})
    requirers: defaultdict[str, set[str]] = defaultdict(set)
    for group, entries in manifest.get("dependency-groups", {}).items():
        for entry in entries:
            requirers[entry["name"]].add(f"{group} group")
    for package in lock.get("package", []):
        for required in package_requirements(package):
            requirers[required].add(package["name"])
    return LockGraph(dict(requirers), set(manifest.get("members", [])))


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


def package_changes(
    old_versions: dict[str, str], new_versions: dict[str, str], old_graph: LockGraph, new_graph: LockGraph
) -> list[PackageChange]:
    """The packages whose version differs; a removed package only has paths in the old lock."""
    changes = []
    for name in sorted(old_versions.keys() | new_versions.keys()):
        old_version, new_version = old_versions.get(name), new_versions.get(name)
        if old_version == new_version:
            continue
        graph = old_graph if new_version is None else new_graph
        changes.append(PackageChange(name, old_version, new_version, graph.links_to(name)))
    return changes


def comes_from(change: PackageChange) -> str:
    """Name the workspace packages and groups behind a change, and the packages that link it to them."""
    required = {child for _, child in change.links}
    roots = {requirer for requirer, _ in change.links} - required
    via = sorted(requirer for requirer, child in change.links if child == change.name and requirer not in roots)
    origins = ", ".join(f"`{root}`" for root in sorted(roots)) or MISSING
    if not via:
        return origins
    shown = ", ".join(f"`{name}`" for name in via[:MAX_VIA_SHOWN])
    hidden = len(via) - MAX_VIA_SHOWN
    return f"{origins} via {shown}" + (f" and {hidden} more" if hidden > 0 else "")


def table_row(change: PackageChange, declared: set[str]) -> str:
    origin = "direct" if canonical_name(change.name) in declared else "transitive"
    old_version, new_version = change.old_version or MISSING, change.new_version or MISSING
    return f"| `{change.name}` | {old_version} | {new_version} | {change.kind} | {origin} | {comes_from(change)} |"


def mermaid_graph(changes: list[PackageChange], links: list[Link]) -> str:
    changes_by_name = {change.name: change for change in changes}
    required = {child for _, child in links}
    names = sorted({name for link in links for name in link} | changes_by_name.keys())
    node_ids = {name: f"n{index}" for index, name in enumerate(names)}

    def node_line(name: str) -> str:
        if change := changes_by_name.get(name):
            return f'{node_ids[name]}["{change.graph_label}"]:::{change.kind}'
        return f'{node_ids[name]}["{name}"]:::{"between" if name in required else "root"}'

    return "\n".join(
        (
            "flowchart LR",
            *GRAPH_STYLES,
            *(node_line(name) for name in names),
            *(f"{node_ids[requirer]} --> {node_ids[child]}" for requirer, child in links),
        )
    )


def graph_section(changes: list[PackageChange]) -> str:
    links = sorted({link for change in changes for link in change.links})
    if not links:
        return ""
    graph = f"```mermaid\n{mermaid_graph(changes, links)}\n```"
    if len(links) <= COLLAPSE_GRAPH_ABOVE_LINKS:
        return f"\n\n{graph}"
    return f"\n\n<details><summary>Dependency graph ({len(links)} links)</summary>\n\n{graph}\n\n</details>"


def render_comment(changes: list[PackageChange], declared: set[str]) -> str:
    if not changes:
        return f"{MARKER}\n### 🔒 uv.lock changes\n\nNo package changes in `uv.lock` for the current revision."
    header = (
        f"{MARKER}\n### 🔒 uv.lock changes ({len(changes)} package{'s' if len(changes) != 1 else ''})\n\n"
        "| Package | Old | New | Change | Origin | Comes from |\n|---|---|---|---|---|---|\n"
    )
    return header + "\n".join(table_row(change, declared) for change in changes) + graph_section(changes)


def comment_lock_diff() -> int:
    environment = ci_env.ci_environment(CI_ENV_VARS)
    if environment is None:
        return 1
    root = runner.repo_root()
    try:
        old_lock = base_lock_content(environment["CI_MERGE_REQUEST_DIFF_BASE_SHA"], root) or ""
        new_lock = (root / "uv.lock").read_text()
        changes = package_changes(
            package_versions(old_lock), package_versions(new_lock), lock_graph(old_lock), lock_graph(new_lock)
        )
        ci_env.api_from(environment).upsert_merge_request_note(
            environment["CI_PROJECT_ID"],
            environment["CI_MERGE_REQUEST_IID"],
            MARKER,
            render_comment(changes, declared_dependencies(root)),
        )
    except subprocess.CalledProcessError as error:
        console.error(f"git failed with exit code {error.returncode}.")
        return 1
    except httpx.HTTPError as error:
        console.error(f"GitLab API error: {gitlab_client.describe_error(error)}")
        return 1
    console.success(f"Lock-diff comment reflects {len(changes)} changed package(s).")
    return 0
