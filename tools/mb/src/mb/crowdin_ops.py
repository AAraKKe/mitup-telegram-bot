"""Two-way synchronization between the gettext catalogs and the Crowdin project.

Push uploads the English source catalog (Crowdin diffs revisions server-side; only
modified strings lose their approval) and then uploads the repo translation for every
string that is not approved in Crowdin and differs from Crowdin's current suggestion —
new strings and re-translated changed strings alike — so reviewers always see the
repo's latest (AI-drafted) text as the newest suggestion. Approved translations are
authoritative on the Crowdin side: push never competes with them, and pull merges them
back into the per-language catalogs.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from mitup_bot.translations import SUPPORTED_LANGUAGES

from . import console, locales_ops

API_BASE_URL = "https://api.crowdin.com/api/v2"
PROJECT_ID = 724481
SOURCE_FILE_NAME = "en.po"
TOKEN_ENV_VAR = "CROWDIN_API_KEY"
REQUEST_TIMEOUT_S = 60.0

# Repo locale code -> Crowdin language id, as configured in the Crowdin project.
CROWDIN_LANGUAGE_IDS = {
    "de_DE": "de",
    "es_ES": "es-ES",
    "gl_ES": "gl",
    "it_IT": "it",
    "pt_BR": "pt-BR",
}

# Minimal valid catalog header for the filtered files uploaded as translations.
UPLOAD_PO_HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"'

PO_UNESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


class CrowdinSyncError(RuntimeError): ...


# --- .po parsing and rendering ---


def po_escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\n", "\\n").replace("\t", "\\t")


def po_unquote(quoted: str) -> str:
    """Decode one quoted .po segment, e.g. '"a\\nb"' -> 'a<newline>b'."""
    body = quoted.strip()[1:-1]
    decoded: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            decoded.append(PO_UNESCAPES.get(body[index + 1], body[index + 1]))
            index += 2
        else:
            decoded.append(body[index])
            index += 1
    return "".join(decoded)


def parse_po_entries(text: str) -> dict[str, str]:
    """Map msgid -> msgstr for every entry, decoding escapes and joining wrapped lines.

    The catalog header (empty msgid) is skipped; comments and metadata are ignored.
    """
    entries: dict[str, str] = {}
    entry: dict[str, str] = {}
    field: str | None = None

    def flush():
        if entry.get("msgid"):
            entries[entry["msgid"]] = entry.get("msgstr", "")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("msgid "):
            flush()
            entry = {"msgid": po_unquote(line.removeprefix("msgid "))}
            field = "msgid"
        elif line.startswith("msgstr "):
            entry["msgstr"] = po_unquote(line.removeprefix("msgstr "))
            field = "msgstr"
        elif line.startswith('"') and field is not None:
            entry[field] += po_unquote(line)
        else:
            field = None
    flush()
    return entries


def render_upload_po(entries: dict[str, str]) -> str:
    blocks = [UPLOAD_PO_HEADER] + [
        f'msgid "{po_escape(msgid)}"\nmsgstr "{po_escape(value)}"' for msgid, value in entries.items()
    ]
    return "\n\n".join(blocks) + "\n"


def replace_msgstr(block: list[str], value: str) -> list[str]:
    """Rewrite a block's msgstr as a single escaped line, dropping wrapped continuations."""
    updated: list[str] = []
    dropping_continuations = False
    for line in block:
        if line.startswith("msgstr "):
            updated.append(f'msgstr "{po_escape(value)}"')
            dropping_continuations = True
        elif dropping_continuations and line.strip().startswith('"'):
            continue
        else:
            updated.append(line)
            dropping_continuations = False
    return updated


def apply_translations(po_text: str, translations: dict[str, str]) -> tuple[str, list[str]]:
    """Merge *translations* into a catalog, returning the new text and the affected msgids.

    Existing entries are updated in place; msgids the catalog doesn't carry yet are
    appended as new blocks so freshly translated strings flow in without a manual step.
    """
    remaining = dict(translations)
    changed: list[str] = []
    updated_blocks: list[list[str]] = []
    for block in locales_ops.parse_po_blocks(po_text):
        updated = block
        for msgid, msgstr in parse_po_entries("\n".join(block)).items():
            new_value = remaining.pop(msgid, None)
            if new_value is not None and new_value != msgstr:
                updated = replace_msgstr(block, new_value)
                changed.append(msgid)
        updated_blocks.append(updated)
    for msgid, value in remaining.items():
        updated_blocks.append([f'msgid "{po_escape(msgid)}"', f'msgstr "{po_escape(value)}"'])
        changed.append(msgid)
    return "\n\n".join("\n".join(block) for block in updated_blocks) + "\n", changed


# --- Crowdin API client ---


@dataclass
class CrowdinClient:
    """Thin async wrapper over the Crowdin v2 REST API, scoped to one project.

    Download URLs are pre-signed S3 links that reject requests carrying an
    Authorization header, hence the separate unauthenticated client.
    """

    api: httpx.AsyncClient
    downloads: httpx.AsyncClient

    async def add_storage(self, filename: str, content: bytes) -> int:
        response = await self.api.post(
            "/storages",
            content=content,
            headers={"Crowdin-API-FileName": filename, "Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()
        return response.json()["data"]["id"]

    async def source_file_id(self) -> int:
        response = await self.api.get(f"/projects/{PROJECT_ID}/files")
        response.raise_for_status()
        for file in response.json()["data"]:
            if file["data"]["name"] == SOURCE_FILE_NAME:
                return file["data"]["id"]
        raise CrowdinSyncError(f"No file named {SOURCE_FILE_NAME!r} in Crowdin project {PROJECT_ID}.")

    async def update_source(self, file_id: int, storage_id: int):
        response = await self.api.put(
            f"/projects/{PROJECT_ID}/files/{file_id}",
            json={"storageId": storage_id, "updateOption": "keep_translations"},
        )
        response.raise_for_status()

    async def export_language(self, file_id: int, language_id: str, *, approved_only: bool) -> dict[str, str]:
        # Without skipUntranslatedStrings, Crowdin fills every string that misses the filter
        # (untranslated, or unapproved under exportApprovedOnly) with the ENGLISH source text
        # instead of leaving msgstr empty — indistinguishable from a real translation.
        response = await self.api.post(
            f"/projects/{PROJECT_ID}/translations/exports",
            json={
                "targetLanguageId": language_id,
                "fileIds": [file_id],
                "exportApprovedOnly": approved_only,
                "skipUntranslatedStrings": True,
            },
        )
        response.raise_for_status()
        download = await self.downloads.get(response.json()["data"]["url"])
        download.raise_for_status()
        return parse_po_entries(download.text)

    async def upload_language(self, file_id: int, language_id: str, storage_id: int):
        response = await self.api.post(
            f"/projects/{PROJECT_ID}/translations/{language_id}",
            json={
                "storageId": storage_id,
                "fileId": file_id,
                "importEqSuggestions": True,
                "autoApproveImported": False,
            },
        )
        response.raise_for_status()


@asynccontextmanager
async def crowdin_client(token: str) -> AsyncIterator[CrowdinClient]:
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx.AsyncClient(base_url=API_BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT_S) as api,
        httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as downloads,
    ):
        yield CrowdinClient(api=api, downloads=downloads)


# --- push / pull orchestration ---


def api_token() -> str | None:
    if token := os.environ.get(TOKEN_ENV_VAR):
        return token
    console.error(f"{TOKEN_ENV_VAR} is not set — create a Crowdin personal access token and export it.")
    return None


def target_languages() -> list[str]:
    languages = [language for language in SUPPORTED_LANGUAGES if language != "en"]
    if unmapped := [language for language in languages if language not in CROWDIN_LANGUAGE_IDS]:
        raise CrowdinSyncError(f"No Crowdin language id configured for {unmapped}; update CROWDIN_LANGUAGE_IDS.")
    return languages


def run_operation(operation: Coroutine[object, object, int]) -> int:
    try:
        return asyncio.run(operation)
    except httpx.HTTPStatusError as error:
        console.error(f"Crowdin API error {error.response.status_code}: {error.response.text}")
        return 1
    except (httpx.HTTPError, CrowdinSyncError) as error:
        console.error(str(error))
        return 1


async def push_language(client: CrowdinClient, file_id: int, language: str, dry_run: bool):
    language_id = CROWDIN_LANGUAGE_IDS[language]
    current, approved = await asyncio.gather(
        client.export_language(file_id, language_id, approved_only=False),
        client.export_language(file_id, language_id, approved_only=True),
    )
    repo_path = locales_ops.po_file_for_language(language)
    repo_entries = parse_po_entries(repo_path.read_text(encoding="utf-8"))
    # A string follows the repo until a reviewer approves a translation: upload wherever the
    # repo has text, Crowdin has no approval, and Crowdin's current suggestion differs.
    outdated = {
        msgid: value
        for msgid, value in repo_entries.items()
        if value and not approved.get(msgid) and current.get(msgid) != value
    }
    if not outdated:
        console.success(f"{language}: Crowdin already carries every unreviewed repo translation")
        return
    if dry_run:
        console.info(f"{language}: would upload {len(outdated)} translation(s): {sorted(outdated)}")
        return
    storage_id = await client.add_storage(f"{language}.po", render_upload_po(outdated).encode())
    await client.upload_language(file_id, language_id, storage_id)
    console.success(f"{language}: uploaded {len(outdated)} translation(s) for review")


async def push_all(token: str, dry_run: bool) -> int:
    async with crowdin_client(token) as client:
        file_id = await client.source_file_id()
        source_path = locales_ops.po_file_for_language("en")
        if dry_run:
            console.info(f"Would push {source_path} (updateOption=keep_translations)")
        else:
            storage_id = await client.add_storage(SOURCE_FILE_NAME, source_path.read_bytes())
            await client.update_source(file_id, storage_id)
            console.success("English source catalog pushed; changed strings await re-approval.")
        await asyncio.gather(*(push_language(client, file_id, language, dry_run) for language in target_languages()))
    return 0


async def pull_language(client: CrowdinClient, file_id: int, language: str, dry_run: bool) -> int:
    language_id = CROWDIN_LANGUAGE_IDS[language]
    exported = await client.export_language(file_id, language_id, approved_only=True)
    approved = {msgid: value for msgid, value in exported.items() if value}
    po_path = locales_ops.po_file_for_language(language)
    updated_text, changed = apply_translations(po_path.read_text(encoding="utf-8"), approved)
    if not changed:
        console.success(f"{language}: catalog already matches the approved Crowdin translations")
        return 0
    if dry_run:
        console.info(f"{language}: would update {len(changed)} translation(s): {sorted(changed)}")
        return len(changed)
    po_path.write_text(updated_text, encoding="utf-8")
    console.success(f"{language}: updated {len(changed)} translation(s)")
    return len(changed)


async def pull_all(token: str, dry_run: bool) -> int:
    async with crowdin_client(token) as client:
        file_id = await client.source_file_id()
        changed_counts = await asyncio.gather(
            *(pull_language(client, file_id, language, dry_run) for language in target_languages())
        )
    if dry_run or not any(changed_counts):
        return 0
    return locales_ops.compile_locales()


def push_catalogs(dry_run: bool = False) -> int:
    if token := api_token():
        return run_operation(push_all(token, dry_run))
    return 1


def pull_catalogs(dry_run: bool = False) -> int:
    if token := api_token():
        return run_operation(pull_all(token, dry_run))
    return 1
