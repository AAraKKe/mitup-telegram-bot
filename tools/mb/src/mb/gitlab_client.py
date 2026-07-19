"""Generic GitLab REST client. Callers supply the API URL and token, whatever their setup."""

from typing import Any

import httpx

REQUEST_TIMEOUT_S = 30.0
NOTES_PER_PAGE = 100


class GitLabApi:
    """GitLab REST client owning endpoint paths, the token header, timeouts, and status checks.

    Callers speak in domain calls (merge requests, notes, users) and never build API URLs
    themselves.
    """

    def __init__(self, api_v4_url: str, token: str):
        self.api_v4_url = api_v4_url
        self.headers = {"PRIVATE-TOKEN": token}

    def open_merge_requests(self, project_id: str, source_branch: str) -> list[Any]:
        merge_requests = self.get(
            f"/projects/{project_id}/merge_requests", params={"state": "opened", "source_branch": source_branch}
        )
        assert isinstance(merge_requests, list)
        return merge_requests

    def current_user(self) -> dict[str, Any]:
        user = self.get("/user")
        assert isinstance(user, dict)
        return user

    def upsert_merge_request_note(self, project_id: str, merge_request_iid: str, marker: str, body: str):
        """Update the MR note whose body contains *marker*, or create it when none exists."""
        notes_path = f"/projects/{project_id}/merge_requests/{merge_request_iid}/notes"
        page = 1
        while True:
            notes = self.get(notes_path, params={"per_page": NOTES_PER_PAGE, "page": page})
            assert isinstance(notes, list)
            if marked := [note for note in notes if marker in (note.get("body") or "")]:
                self.put(f"{notes_path}/{marked[0]['id']}", json={"body": body})
                return
            if len(notes) < NOTES_PER_PAGE:
                break
            page += 1
        self.post(notes_path, json={"body": body})

    def get(self, path: str, params: dict[str, int | str] | None = None) -> Any:
        response = httpx.get(f"{self.api_v4_url}{path}", params=params, headers=self.headers, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, json: dict[str, str]):
        response = httpx.post(f"{self.api_v4_url}{path}", json=json, headers=self.headers, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()

    def put(self, path: str, json: dict[str, str]):
        response = httpx.put(f"{self.api_v4_url}{path}", json=json, headers=self.headers, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()


def describe_error(error: httpx.HTTPError) -> str:
    """Error class and status code only — the full message may echo response content into public CI logs."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"{type(error).__name__} (HTTP {error.response.status_code})"
    return type(error).__name__
