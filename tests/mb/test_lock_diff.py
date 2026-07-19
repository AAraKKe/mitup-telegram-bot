import subprocess
from pathlib import Path

import httpx
import pytest

from mb import console, gitlab_client, lock_diff, runner

API_V4_URL = "https://gitlab.example/api/v4"
PROJECT_ID = "724481"
MR_IID = "42"
BASE_SHA = "abc123def456"
TOKEN = "glpat-SECRETVALUE"

FULL_ENV = {
    "MITUP_GITLAB_TOKEN": TOKEN,
    "CI_API_V4_URL": API_V4_URL,
    "CI_PROJECT_ID": PROJECT_ID,
    "CI_MERGE_REQUEST_IID": MR_IID,
    "CI_MERGE_REQUEST_DIFF_BASE_SHA": BASE_SHA,
}

NOTES_URL = f"{API_V4_URL}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"

OLD_LOCK = """
[[package]]
name = "httpx"
version = "0.27.0"

[[package]]
name = "anyio"
version = "4.3.0"

[[package]]
name = "sniffio"
version = "1.3.0"
"""

NEW_LOCK = """
[[package]]
name = "httpx"
version = "0.27.2"

[[package]]
name = "anyio"
version = "4.3.0"

[[package]]
name = "idna"
version = "3.7"
"""


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch):
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def set_ci_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None):
    for name, value in {**FULL_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def write_workspace(root: Path, dependencies: list[str]):
    dependency_list = ", ".join(f'"{dependency}"' for dependency in dependencies)
    (root / "pyproject.toml").write_text(f'[project]\nname = "root"\ndependencies = [{dependency_list}]\n')


# --- parsing ---


def test_package_versions_skips_entries_without_version():
    lock = '[[package]]\nname = "workspace-member"\n\n[[package]]\nname = "httpx"\nversion = "0.27.0"\n'
    assert lock_diff.package_versions(lock) == {"httpx": "0.27.0"}


def test_declared_dependencies_covers_all_member_sections(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "root"\n'
        'dependencies = ["httpx>=0.27"]\n'
        "[project.optional-dependencies]\n"
        'extra = ["Typing_Extensions"]\n'
        "[dependency-groups]\n"
        'dev = ["ruff==0.5.0"]\n'
        "[tool.uv.workspace]\n"
        'members = ["libs/*"]\n'
    )
    member = tmp_path / "libs" / "core"
    member.mkdir(parents=True)
    (member / "pyproject.toml").write_text('[project]\nname = "member"\ndependencies = ["structlog ~= 24.0"]\n')
    (tmp_path / "libs" / "empty").mkdir()  # a member match without a pyproject must be skipped, not crash

    declared = lock_diff.declared_dependencies(tmp_path)

    assert declared == {"httpx", "typing-extensions", "ruff", "structlog"}


# --- diff and rendering ---


def test_diff_lines_reports_changed_added_and_removed():
    old_versions = lock_diff.package_versions(OLD_LOCK)
    new_versions = lock_diff.package_versions(NEW_LOCK)

    lines = lock_diff.diff_lines(old_versions, new_versions, declared={"httpx"})

    assert lines == [
        "| `httpx` | 0.27.0 | 0.27.2 | changed | direct |",
        "| `idna` | — | 3.7 | added | transitive |",
        "| `sniffio` | 1.3.0 | — | removed | transitive |",
    ]


def test_diff_lines_normalizes_names_against_declared():
    lines = lock_diff.diff_lines({}, {"typing-extensions": "4.12.0"}, declared={"typing-extensions"})

    assert lines == ["| `typing-extensions` | — | 4.12.0 | added | direct |"]


def test_render_comment_without_changes_still_carries_marker():
    body = lock_diff.render_comment([])

    assert body.startswith(lock_diff.MARKER)
    assert "No package changes" in body


# --- note upsert ---


class NotesApi:
    """Serves the notes list endpoint and records the write that follows."""

    def __init__(self, pages: list[list[dict[str, object]]]):
        self.pages = pages
        self.method: str | None = None
        self.url: str | None = None
        self.body: str | None = None

    def get(self, url: str, *, params: dict[str, int], headers: dict[str, str], timeout: float) -> httpx.Response:
        page_index = params["page"] - 1
        payload = self.pages[page_index] if page_index < len(self.pages) else []
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    def put(self, url: str, *, json: dict[str, str], headers: dict[str, str], timeout: float) -> httpx.Response:
        self.method, self.url, self.body = "PUT", url, json["body"]
        return httpx.Response(200, json={}, request=httpx.Request("PUT", url))

    def post(self, url: str, *, json: dict[str, str], headers: dict[str, str], timeout: float) -> httpx.Response:
        self.method, self.url, self.body = "POST", url, json["body"]
        return httpx.Response(201, json={}, request=httpx.Request("POST", url))


def install_notes_api(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, object]]]) -> NotesApi:
    api = NotesApi(pages)
    monkeypatch.setattr(gitlab_client.httpx, "get", api.get)
    monkeypatch.setattr(gitlab_client.httpx, "put", api.put)
    monkeypatch.setattr(gitlab_client.httpx, "post", api.post)
    return api


def upsert(marker: str, body: str):
    gitlab_client.GitLabApi(API_V4_URL, TOKEN).upsert_merge_request_note(PROJECT_ID, MR_IID, marker, body)


def test_upsert_note_updates_the_marked_note_in_place(monkeypatch: pytest.MonkeyPatch):
    api = install_notes_api(
        monkeypatch,
        [[{"id": 7, "body": "unrelated"}, {"id": 9, "body": f"{lock_diff.MARKER}\nstale table"}]],
    )

    upsert(lock_diff.MARKER, "fresh body")

    assert api.method == "PUT"
    assert api.url == f"{NOTES_URL}/9"
    assert api.body == "fresh body"


def test_upsert_note_creates_a_note_when_no_marker_exists(monkeypatch: pytest.MonkeyPatch):
    api = install_notes_api(monkeypatch, [[{"id": 7, "body": "unrelated"}]])

    upsert(lock_diff.MARKER, "fresh body")

    assert api.method == "POST"
    assert api.url == NOTES_URL
    assert api.body == "fresh body"


def test_upsert_note_walks_pages_to_find_the_marker(monkeypatch: pytest.MonkeyPatch):
    full_page: list[dict[str, object]] = [
        {"id": index, "body": "unrelated"} for index in range(gitlab_client.NOTES_PER_PAGE)
    ]
    marked_page: list[dict[str, object]] = [{"id": 999, "body": lock_diff.MARKER}]
    api = install_notes_api(monkeypatch, [full_page, marked_page])

    upsert(lock_diff.MARKER, "fresh body")

    assert api.method == "PUT"
    assert api.url == f"{NOTES_URL}/999"


# --- comment_lock_diff ---


def install_git(monkeypatch: pytest.MonkeyPatch, base_lock: str | None, fail_fetch: bool = False):
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[1] == "fetch":
            if fail_fetch:
                raise subprocess.CalledProcessError(returncode=128, cmd=list(args))
            return subprocess.CompletedProcess(args, 0)
        assert args[1] == "show"
        if base_lock is None:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="path not found")
        return subprocess.CompletedProcess(args, 0, stdout=base_lock, stderr="")

    monkeypatch.setattr(lock_diff.subprocess, "run", fake_run)


def test_comment_lock_diff_posts_the_delta(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    set_ci_env(monkeypatch)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    write_workspace(tmp_path, ["httpx"])
    (tmp_path / "uv.lock").write_text(NEW_LOCK)
    install_git(monkeypatch, OLD_LOCK)
    api = install_notes_api(monkeypatch, [[]])

    assert lock_diff.comment_lock_diff() == 0

    assert api.body is not None
    assert "| `httpx` | 0.27.0 | 0.27.2 | changed | direct |" in api.body
    assert "3 packages" in api.body
    assert "3 changed package(s)" in combined(capsys)


def test_comment_lock_diff_treats_missing_base_lock_as_all_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    set_ci_env(monkeypatch)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    write_workspace(tmp_path, [])
    (tmp_path / "uv.lock").write_text(NEW_LOCK)
    install_git(monkeypatch, None)
    api = install_notes_api(monkeypatch, [[]])

    assert lock_diff.comment_lock_diff() == 0

    assert api.body is not None
    assert "| `httpx` | — | 0.27.2 | added | transitive |" in api.body


def test_comment_lock_diff_requires_the_ci_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    set_ci_env(monkeypatch, MITUP_GITLAB_TOKEN=None)

    assert lock_diff.comment_lock_diff() == 1
    assert "MITUP_GITLAB_TOKEN" in combined(capsys)


def test_comment_lock_diff_reports_git_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    set_ci_env(monkeypatch)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    write_workspace(tmp_path, [])
    (tmp_path / "uv.lock").write_text(NEW_LOCK)
    install_git(monkeypatch, OLD_LOCK, fail_fetch=True)

    assert lock_diff.comment_lock_diff() == 1
    assert "git failed with exit code 128" in combined(capsys)


def test_comment_lock_diff_reports_api_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    set_ci_env(monkeypatch)
    monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
    write_workspace(tmp_path, [])
    (tmp_path / "uv.lock").write_text(NEW_LOCK)
    install_git(monkeypatch, OLD_LOCK)

    def failing_get(url: str, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(gitlab_client.httpx, "get", failing_get)

    assert lock_diff.comment_lock_diff() == 1
    assert "GitLab API error" in combined(capsys)
