import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast
from unittest import mock

import httpx
import pytest
from mb.locales import app
from typer.testing import CliRunner

from mb import console, crowdin_ops, locales_ops

cli = CliRunner()

RequestHandler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch):
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


class FakeCrowdinClient:
    """Stand-in for CrowdinClient that records calls and serves canned exports by approval flag.

    ``current`` answers ``approved_only=False`` (the live suggestions) and ``approved`` answers
    ``approved_only=True`` (the reviewer-approved set); either defaults to an empty catalog.
    """

    def __init__(
        self,
        current: dict[str, str] | None = None,
        approved: dict[str, str] | None = None,
        file_id: int = 42,
    ):
        self.current = current or {}
        self.approved = approved or {}
        self.file_id = file_id
        self.export_calls: list[tuple[int, str, bool]] = []
        self.storage_uploads: list[tuple[str, bytes]] = []
        self.language_uploads: list[tuple[int, str, int]] = []
        self.source_updates: list[tuple[int, int]] = []

    async def source_file_id(self) -> int:
        return self.file_id

    async def update_source(self, file_id: int, storage_id: int):
        self.source_updates.append((file_id, storage_id))

    async def export_language(self, file_id: int, language_id: str, *, approved_only: bool) -> dict[str, str]:
        self.export_calls.append((file_id, language_id, approved_only))
        return self.approved if approved_only else self.current

    async def add_storage(self, filename: str, content: bytes) -> int:
        self.storage_uploads.append((filename, content))
        return 777

    async def upload_language(self, file_id: int, language_id: str, storage_id: int):
        self.language_uploads.append((file_id, language_id, storage_id))


@asynccontextmanager
async def mock_transport_client(
    api_handler: RequestHandler, downloads_handler: RequestHandler | None = None
) -> AsyncIterator[crowdin_ops.CrowdinClient]:
    """Build a real CrowdinClient whose two httpx clients are backed by MockTransports."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    async with (
        httpx.AsyncClient(base_url=crowdin_ops.API_BASE_URL, transport=httpx.MockTransport(api_handler)) as api,
        httpx.AsyncClient(transport=httpx.MockTransport(downloads_handler or refuse)) as downloads,
    ):
        yield crowdin_ops.CrowdinClient(api=api, downloads=downloads)


def yielding(client: FakeCrowdinClient) -> Callable[[str], AbstractAsyncContextManager[FakeCrowdinClient]]:
    """A crowdin_client replacement that hands orchestration the recording fake."""

    @asynccontextmanager
    async def factory(token: str) -> AsyncIterator[FakeCrowdinClient]:
        yield client

    return factory


# --- po_escape / po_unquote ---


@pytest.mark.parametrize(
    "value, escaped",
    [
        ("plain", "plain"),
        ("a\nb", "a\\nb"),
        ("tab\there", "tab\\there"),
        ('say "hi"', 'say \\"hi\\"'),
        ("back\\slash", "back\\\\slash"),
    ],
    ids=["plain", "newline", "tab", "quote", "backslash"],
)
def test_po_escape(value: str, escaped: str):
    assert crowdin_ops.po_escape(value) == escaped


@pytest.mark.parametrize(
    "quoted, decoded",
    [
        ('"plain"', "plain"),
        ('"a\\nb"', "a\nb"),
        ('"tab\\there"', "tab\there"),
        ('"say \\"hi\\""', 'say "hi"'),
        ('"back\\\\slash"', "back\\slash"),
        ('"\\q"', "q"),  # unknown escape drops the backslash and keeps the char
    ],
    ids=["plain", "newline", "tab", "quote", "backslash", "unknown_escape"],
)
def test_po_unquote(quoted: str, decoded: str):
    assert crowdin_ops.po_unquote(quoted) == decoded


@pytest.mark.parametrize(
    "value",
    ["plain", "a\nb", "tab\there", 'quote " mark', "back\\slash", 'mix\t"\\\nend', ""],
    ids=["plain", "newline", "tab", "quote", "backslash", "mix", "empty"],
)
def test_po_escape_unquote_round_trip(value: str):
    assert crowdin_ops.po_unquote(f'"{crowdin_ops.po_escape(value)}"') == value


# --- parse_po_entries ---


def test_parse_po_entries_joins_wrapped_and_skips_metadata():
    text = (
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        "\n"
        "# a translator comment\n"
        'msgid "greeting"\n'
        'msgstr "hello"\n'
        "\n"
        'msgid "wrapped"\n'
        'msgstr ""\n'
        '"line one "\n'
        '"line two "\n'
        '"line three"\n'
        "\n"
        'msgid "escaped"\n'
        'msgstr "a\\nb\\tc"\n'
    )

    entries = crowdin_ops.parse_po_entries(text)

    assert entries == {
        "greeting": "hello",
        "wrapped": "line one line two line three",
        "escaped": "a\nb\tc",
    }


# --- render_upload_po ---


def test_render_upload_po_round_trips_entries():
    entries = {"hi": "hola", "multi\nline": "salto\nde"}

    catalog = crowdin_ops.render_upload_po(entries)

    assert catalog.startswith(crowdin_ops.UPLOAD_PO_HEADER)
    assert catalog.endswith("\n")
    assert 'msgid "multi\\nline"' in catalog  # the literal newline is escaped in the rendered source
    assert crowdin_ops.parse_po_entries(catalog) == entries


# --- replace_msgstr ---


def test_replace_msgstr_drops_only_msgstr_continuations():
    block = ['msgid ""', '"multi id"', 'msgstr "old"', '"cont one"', '"cont two"']

    # The msgid continuation line survives; only the wrapped msgstr lines are collapsed.
    assert crowdin_ops.replace_msgstr(block, "new") == ['msgid ""', '"multi id"', 'msgstr "new"']


def test_replace_msgstr_escapes_value():
    block = ['msgid "k"', 'msgstr "old"']

    assert crowdin_ops.replace_msgstr(block, "line\nbreak") == ['msgid "k"', 'msgstr "line\\nbreak"']


# --- apply_translations ---


def test_apply_translations_updates_in_place_preserving_lines():
    po_text = 'msgid ""\nmsgstr ""\n\n# translator note\nmsgid "hi"\nmsgstr "old"\n'

    updated_text, changed = crowdin_ops.apply_translations(po_text, {"hi": "new"})

    assert changed == ["hi"]
    assert "# translator note" in updated_text
    assert 'msgstr "new"' in updated_text
    assert 'msgstr "old"' not in updated_text


def test_apply_translations_collapses_wrapped_msgstr():
    po_text = 'msgid ""\nmsgstr ""\n\nmsgid "long"\nmsgstr ""\n"part one "\n"part two"\n'

    updated_text, changed = crowdin_ops.apply_translations(po_text, {"long": "brand new"})

    assert changed == ["long"]
    assert 'msgstr "brand new"' in updated_text
    assert '"part one "' not in updated_text
    assert '"part two"' not in updated_text


def test_apply_translations_appends_missing_msgid():
    po_text = 'msgid ""\nmsgstr ""\n\nmsgid "exists"\nmsgstr "here"\n'

    updated_text, changed = crowdin_ops.apply_translations(po_text, {"exists": "here", "fresh": "added"})

    assert changed == ["fresh"]
    assert updated_text.endswith('msgid "fresh"\nmsgstr "added"\n')


def test_apply_translations_no_changes_returns_input():
    po_text = 'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "b"\n'

    updated_text, changed = crowdin_ops.apply_translations(po_text, {"a": "b"})

    assert changed == []
    assert updated_text == po_text


def test_apply_translations_leaves_untargeted_entries():
    po_text = 'msgid ""\nmsgstr ""\n\nmsgid "keep"\nmsgstr "original"\n\nmsgid "change"\nmsgstr "old"\n'

    updated_text, changed = crowdin_ops.apply_translations(po_text, {"change": "updated"})

    assert changed == ["change"]
    assert 'msgid "keep"\nmsgstr "original"' in updated_text
    assert 'msgstr "updated"' in updated_text


# --- push_language ---


async def test_push_language_uploads_unreviewed_outdated_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_path = tmp_path / "es_ES.po"
    repo_path.write_text(
        'msgid ""\nmsgstr ""\n\n'
        'msgid "fresh"\nmsgstr "repo-fresh"\n\n'
        'msgid "changed"\nmsgstr "repo-changed"\n\n'
        'msgid "idempotent"\nmsgstr "repo-same"\n\n'
        'msgid "approved"\nmsgstr "repo-approved"\n\n'
        'msgid "empty_repo"\nmsgstr ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: repo_path)
    client = FakeCrowdinClient(
        current={"changed": "crowdin-stale", "idempotent": "repo-same", "approved": "crowdin-appr"},
        approved={"approved": "crowdin-appr"},
    )

    await crowdin_ops.push_language(cast(crowdin_ops.CrowdinClient, client), 42, "es_ES", dry_run=False)

    # Both exports are gathered; assert as a sorted pair since the flags race independently.
    assert sorted(client.export_calls) == [(42, "es-ES", False), (42, "es-ES", True)]
    assert len(client.storage_uploads) == 1
    filename, content = client.storage_uploads[0]
    assert filename == "es_ES.po"
    # "fresh" is absent from Crowdin and "changed" has a differing unapproved suggestion, so both upload.
    # "idempotent" already matches the current suggestion, "approved" is reviewer-owned, and "empty_repo"
    # has no repo text — all three are skipped.
    assert crowdin_ops.parse_po_entries(content.decode()) == {
        "fresh": "repo-fresh",
        "changed": "repo-changed",
    }
    assert client.language_uploads == [(42, "es-ES", 777)]


@pytest.mark.parametrize(
    "current_value, approved_value, uploaded",
    [
        (None, None, True),
        ("crowdin-stale", None, True),
        ("repo-value", None, False),
        ("crowdin-stale", "crowdin-final", False),
        ("repo-value", "repo-value", False),
        ("crowdin-stale", "", True),
    ],
    ids=["new", "changed_unapproved", "idempotent", "approved_differs", "approved_matches", "empty_approval"],
)
async def test_push_language_upload_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_value: str | None,
    approved_value: str | None,
    uploaded: bool,
):
    repo_path = tmp_path / "es_ES.po"
    repo_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "k"\nmsgstr "repo-value"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: repo_path)
    # A None export value means Crowdin carries no entry for the msgid at all under that flag.
    current = {"k": current_value} if current_value is not None else {}
    approved = {"k": approved_value} if approved_value is not None else {}
    client = FakeCrowdinClient(current=current, approved=approved)

    await crowdin_ops.push_language(cast(crowdin_ops.CrowdinClient, client), 42, "es_ES", dry_run=False)

    if uploaded:
        assert crowdin_ops.parse_po_entries(client.storage_uploads[0][1].decode()) == {"k": "repo-value"}
        assert client.language_uploads == [(42, "es-ES", 777)]
    else:
        assert client.storage_uploads == []
        assert client.language_uploads == []


async def test_push_language_skips_when_all_reviewed_or_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    repo_path = tmp_path / "es_ES.po"
    repo_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "AAA"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: repo_path)
    client = FakeCrowdinClient(current={"a": "AAA"})

    await crowdin_ops.push_language(cast(crowdin_ops.CrowdinClient, client), 1, "es_ES", dry_run=False)

    assert client.storage_uploads == []
    assert client.language_uploads == []
    assert "Crowdin already carries every unreviewed repo translation" in combined(capsys)


async def test_push_language_dry_run_uploads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    repo_path = tmp_path / "es_ES.po"
    repo_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "AAA"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: repo_path)
    client = FakeCrowdinClient()

    await crowdin_ops.push_language(cast(crowdin_ops.CrowdinClient, client), 1, "es_ES", dry_run=True)

    assert client.storage_uploads == []
    assert client.language_uploads == []
    assert "would upload 1 translation" in combined(capsys)


# --- pull_language ---


async def test_pull_language_applies_approved_and_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_path = tmp_path / "es_ES.po"
    po_path.write_text(
        'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "old"\n\nmsgid "b"\nmsgstr "keep"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    client = FakeCrowdinClient(approved={"a": "new", "b": "", "c": "brand"})

    changed = await crowdin_ops.pull_language(cast(crowdin_ops.CrowdinClient, client), 5, "es_ES", dry_run=False)

    assert changed == 2  # "a" updated + "c" appended; the empty "b" export is ignored
    assert client.export_calls == [(5, "es-ES", True)]
    content = po_path.read_text(encoding="utf-8")
    assert 'msgstr "new"' in content
    assert 'msgid "c"\nmsgstr "brand"' in content
    assert 'msgstr "keep"' in content
    assert 'msgstr "old"' not in content


async def test_pull_language_no_change_does_not_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    po_path = mock.Mock(spec=Path)
    po_path.read_text.return_value = 'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "done"\n'
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    client = FakeCrowdinClient(approved={"a": "done"})

    changed = await crowdin_ops.pull_language(cast(crowdin_ops.CrowdinClient, client), 1, "es_ES", dry_run=False)

    assert changed == 0
    po_path.write_text.assert_not_called()
    assert "already matches" in combined(capsys)


async def test_pull_language_dry_run_does_not_write(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    po_path = mock.Mock(spec=Path)
    po_path.read_text.return_value = 'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "old"\n'
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    client = FakeCrowdinClient(approved={"a": "new"})

    changed = await crowdin_ops.pull_language(cast(crowdin_ops.CrowdinClient, client), 1, "es_ES", dry_run=True)

    assert changed == 1
    po_path.write_text.assert_not_called()
    assert "would update 1 translation" in combined(capsys)


# --- api_token ---


def test_api_token_returns_value_when_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CROWDIN_API_KEY", "secret-token")

    assert crowdin_ops.api_token() == "secret-token"


def test_api_token_errors_when_unset(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.delenv("CROWDIN_API_KEY", raising=False)

    assert crowdin_ops.api_token() is None
    assert "CROWDIN_API_KEY is not set" in combined(capsys)


# --- target_languages ---


def test_target_languages_excludes_english():
    assert crowdin_ops.target_languages() == ["es_ES", "gl_ES", "de_DE", "pt_BR", "it_IT"]


def test_target_languages_raises_for_unmapped_language(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crowdin_ops, "CROWDIN_LANGUAGE_IDS", {"es_ES": "es-ES"})

    with pytest.raises(crowdin_ops.CrowdinSyncError, match="No Crowdin language id"):
        crowdin_ops.target_languages()


# --- run_operation ---


async def succeed() -> int:
    return 0


async def raise_http_status() -> int:
    request = httpx.Request("GET", "https://crowdin.test/x")
    response = httpx.Response(503, request=request, text="rate limited")
    raise httpx.HTTPStatusError("boom", request=request, response=response)


async def raise_http_error() -> int:
    raise httpx.ConnectError("no route to host")


async def raise_sync_error() -> int:
    raise crowdin_ops.CrowdinSyncError("language mapping missing")


def test_run_operation_returns_result_on_success():
    assert crowdin_ops.run_operation(succeed()) == 0


def test_run_operation_maps_http_status_error(capsys: pytest.CaptureFixture[str]):
    assert crowdin_ops.run_operation(raise_http_status()) == 1
    output = combined(capsys)
    assert "Crowdin API error 503" in output
    assert "rate limited" in output


def test_run_operation_maps_generic_http_error(capsys: pytest.CaptureFixture[str]):
    assert crowdin_ops.run_operation(raise_http_error()) == 1
    assert "no route to host" in combined(capsys)


def test_run_operation_maps_sync_error(capsys: pytest.CaptureFixture[str]):
    assert crowdin_ops.run_operation(raise_sync_error()) == 1
    assert "language mapping missing" in combined(capsys)


# --- push_catalogs / pull_catalogs ---


def test_push_catalogs_without_token_returns_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.delenv("CROWDIN_API_KEY", raising=False)

    assert crowdin_ops.push_catalogs() == 1
    assert "CROWDIN_API_KEY is not set" in combined(capsys)


def test_push_catalogs_with_token_runs_operation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CROWDIN_API_KEY", "tok")
    sentinel = object()
    run_op = mock.Mock(return_value=0)
    monkeypatch.setattr(crowdin_ops, "push_all", lambda token, dry_run: sentinel)
    monkeypatch.setattr(crowdin_ops, "run_operation", run_op)

    assert crowdin_ops.push_catalogs(dry_run=True) == 0
    run_op.assert_called_once_with(sentinel)


def test_pull_catalogs_without_token_returns_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.delenv("CROWDIN_API_KEY", raising=False)

    assert crowdin_ops.pull_catalogs() == 1
    assert "CROWDIN_API_KEY is not set" in combined(capsys)


def test_pull_catalogs_with_token_runs_operation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CROWDIN_API_KEY", "tok")
    sentinel = object()
    run_op = mock.Mock(return_value=0)
    monkeypatch.setattr(crowdin_ops, "pull_all", lambda token, dry_run: sentinel)
    monkeypatch.setattr(crowdin_ops, "run_operation", run_op)

    assert crowdin_ops.pull_catalogs(dry_run=True) == 0
    run_op.assert_called_once_with(sentinel)


# --- push / pull CLI commands ---


def test_push_command_passes_dry_run(monkeypatch: pytest.MonkeyPatch):
    push = mock.Mock(return_value=0)
    monkeypatch.setattr(crowdin_ops, "push_catalogs", push)

    result = cli.invoke(app, ["push", "--dry-run"])

    assert result.exit_code == 0
    push.assert_called_once_with(dry_run=True)


def test_push_command_defaults_to_no_dry_run(monkeypatch: pytest.MonkeyPatch):
    push = mock.Mock(return_value=2)
    monkeypatch.setattr(crowdin_ops, "push_catalogs", push)

    result = cli.invoke(app, ["push"])

    assert result.exit_code == 2
    push.assert_called_once_with(dry_run=False)


def test_pull_command_passes_dry_run(monkeypatch: pytest.MonkeyPatch):
    pull = mock.Mock(return_value=0)
    monkeypatch.setattr(crowdin_ops, "pull_catalogs", pull)

    result = cli.invoke(app, ["pull", "--dry-run"])

    assert result.exit_code == 0
    pull.assert_called_once_with(dry_run=True)


def test_pull_command_defaults_to_no_dry_run(monkeypatch: pytest.MonkeyPatch):
    pull = mock.Mock(return_value=1)
    monkeypatch.setattr(crowdin_ops, "pull_catalogs", pull)

    result = cli.invoke(app, ["pull"])

    assert result.exit_code == 1
    pull.assert_called_once_with(dry_run=False)


# --- CrowdinClient HTTP methods (backed by MockTransport) ---


async def test_source_file_id_finds_matching_file():
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"data": {"id": 11, "name": "other.po"}},
                    {"data": {"id": 22, "name": crowdin_ops.SOURCE_FILE_NAME}},
                ]
            },
        )

    async with mock_transport_client(api_handler) as client:
        assert await client.source_file_id() == 22


async def test_source_file_id_raises_when_source_absent():
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"data": {"id": 11, "name": "other.po"}}]})

    async with mock_transport_client(api_handler) as client:
        with pytest.raises(crowdin_ops.CrowdinSyncError, match="No file named"):
            await client.source_file_id()


async def test_add_storage_posts_content_and_returns_id():
    seen: dict[str, object] = {}

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        seen["filename"] = request.headers["Crowdin-API-FileName"]
        return httpx.Response(201, json={"data": {"id": 99}})

    async with mock_transport_client(api_handler) as client:
        assert await client.add_storage("es_ES.po", b"payload") == 99

    assert seen["content"] == b"payload"
    assert seen["filename"] == "es_ES.po"


async def test_update_source_puts_keep_translations():
    seen: dict[str, object] = {}

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async with mock_transport_client(api_handler) as client:
        await client.update_source(22, 99)

    assert seen["method"] == "PUT"
    assert seen["json"] == {"storageId": 99, "updateOption": "keep_translations"}


async def test_export_language_downloads_and_parses():
    def api_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/translations/exports")
        assert json.loads(request.content)["skipUntranslatedStrings"] is True
        return httpx.Response(200, json={"data": {"url": "https://downloads.crowdin.test/es.po"}})

    def downloads_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "downloads.crowdin.test"
        return httpx.Response(200, text='msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "hola"\n')

    async with mock_transport_client(api_handler, downloads_handler) as client:
        entries = await client.export_language(22, "es-ES", approved_only=True)

    assert entries == {"a": "hola"}


async def test_upload_language_posts_translation():
    seen: dict[str, object] = {}

    def api_handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={})

    async with mock_transport_client(api_handler) as client:
        await client.upload_language(22, "es-ES", 99)

    assert str(seen["path"]).endswith("/translations/es-ES")
    assert seen["json"] == {
        "storageId": 99,
        "fileId": 22,
        "importEqSuggestions": True,
        "autoApproveImported": False,
    }


async def test_add_storage_raises_on_error_status():
    def api_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    async with mock_transport_client(api_handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.add_storage("es_ES.po", b"payload")


async def test_crowdin_client_sets_bearer_auth():
    async with crowdin_ops.crowdin_client("tok123") as client:
        assert str(client.api.base_url) == f"{crowdin_ops.API_BASE_URL}/"  # httpx normalizes a trailing slash
        assert client.api.headers["Authorization"] == "Bearer tok123"
        # Pre-signed download links reject an Authorization header, so that client carries none.
        assert "Authorization" not in client.downloads.headers


# --- push_all / pull_all orchestration ---


async def test_push_all_updates_source_then_seeds_languages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "en.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "A"\n', encoding="utf-8")
    (tmp_path / "es_ES.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "hola"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: tmp_path / f"{lang}.po")
    monkeypatch.setattr(crowdin_ops, "target_languages", lambda: ["es_ES"])
    client = FakeCrowdinClient()  # Crowdin has nothing yet, so the repo string is seeded
    monkeypatch.setattr(crowdin_ops, "crowdin_client", yielding(client))

    assert await crowdin_ops.push_all("tok", dry_run=False) == 0

    assert client.source_updates == [(42, 777)]
    assert client.language_uploads == [(42, "es-ES", 777)]


async def test_push_all_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "en.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "A"\n', encoding="utf-8")
    (tmp_path / "es_ES.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "hola"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: tmp_path / f"{lang}.po")
    monkeypatch.setattr(crowdin_ops, "target_languages", lambda: ["es_ES"])
    client = FakeCrowdinClient()
    monkeypatch.setattr(crowdin_ops, "crowdin_client", yielding(client))

    assert await crowdin_ops.push_all("tok", dry_run=True) == 0

    assert client.source_updates == []
    assert client.storage_uploads == []
    assert client.language_uploads == []


async def test_pull_all_recompiles_when_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_path = tmp_path / "es_ES.po"
    po_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "old"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    monkeypatch.setattr(crowdin_ops, "target_languages", lambda: ["es_ES"])
    compiled = mock.Mock(return_value=0)
    monkeypatch.setattr(locales_ops, "compile_locales", compiled)
    client = FakeCrowdinClient(approved={"a": "new"})
    monkeypatch.setattr(crowdin_ops, "crowdin_client", yielding(client))

    assert await crowdin_ops.pull_all("tok", dry_run=False) == 0

    compiled.assert_called_once()
    assert 'msgstr "new"' in po_path.read_text(encoding="utf-8")


async def test_pull_all_dry_run_skips_recompile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_path = tmp_path / "es_ES.po"
    po_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "old"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    monkeypatch.setattr(crowdin_ops, "target_languages", lambda: ["es_ES"])
    compiled = mock.Mock(return_value=0)
    monkeypatch.setattr(locales_ops, "compile_locales", compiled)
    client = FakeCrowdinClient(approved={"a": "new"})
    monkeypatch.setattr(crowdin_ops, "crowdin_client", yielding(client))

    assert await crowdin_ops.pull_all("tok", dry_run=True) == 0

    compiled.assert_not_called()
    assert 'msgstr "old"' in po_path.read_text(encoding="utf-8")


async def test_pull_all_skips_recompile_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_path = tmp_path / "es_ES.po"
    po_path.write_text('msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "done"\n', encoding="utf-8")
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: po_path)
    monkeypatch.setattr(crowdin_ops, "target_languages", lambda: ["es_ES"])
    compiled = mock.Mock(return_value=0)
    monkeypatch.setattr(locales_ops, "compile_locales", compiled)
    client = FakeCrowdinClient(approved={"a": "done"})
    monkeypatch.setattr(crowdin_ops, "crowdin_client", yielding(client))

    assert await crowdin_ops.pull_all("tok", dry_run=False) == 0

    compiled.assert_not_called()
