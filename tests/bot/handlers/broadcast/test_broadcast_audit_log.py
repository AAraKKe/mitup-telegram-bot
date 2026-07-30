"""The operator side of the broadcast audit trail.

A broadcast is the one irreversible mass-impact action an operator can take, and the rows it works
on are deleted as it goes, so these lines are the only reconstruction of an operator mistake. The
assertions here are about that trail — who authorised what, on which draft, and why a step refused —
not about the screens, which `test_broadcast_*.py` already cover.
"""

import datetime as dt
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from telegram import Chat, Document, Message, Update
from telegram import User as TgUser

from mitup_bot.handlers.broadcast import utils
from mitup_bot.handlers.broadcast.content import DOCUMENT_REJECTED_EVENT, MAX_DOCUMENT_BYTES, UPLOAD_IGNORED_EVENT
from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId
from mitup_bot.handlers.broadcast.utils import DRAFT_DISCARDED_EVENT
from mitup_bot.models import Broadcast, BroadcastMessage, User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils import callbacks as cb
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    create_broadcast,
    log_record,
)

if TYPE_CHECKING:
    from tests.helpers.types import RegisterAuthorDrafts, RegisterMember

ADMIN_TG_ID = 123
CHAT_ID = 123
BROADCAST_ID = 5
BROADCAST_NAME = "Launch news"

PLAIN_YAML = "- language: en\n  message: Hello\n- language: es_ES\n  message: Hola\n"

# The parser detail quotes the operator's own file back at them, so it may reach the reply and never
# the log. This YAML breaks on a line whose text would be echoed in that detail.
UNPARSEABLE_YAML = "- language: en\n  message: [unclosed\n"
UNPARSEABLE_FRAGMENT = "unclosed"


def set_get_result(mock_session: MockDbSession, broadcast: Broadcast | None):
    mock_session.get = mock.AsyncMock(return_value=broadcast)


def patch_member_counts(counts: dict[str, int]) -> mock._patch:
    return mock.patch.object(utils, "count_members_by_language", new=mock.AsyncMock(return_value=counts))


def drafted_broadcast(languages: tuple[str, ...] = ("en",)) -> Broadcast:
    broadcast = create_broadcast(id=BROADCAST_ID, name=BROADCAST_NAME, author_tg_id=ADMIN_TG_ID)
    broadcast.messages = [BroadcastMessage(language=language, body_html="Hello") for language in languages]
    return broadcast


def document_upload(handler_context: HandlerContext, app: StubMitupApp, tg_user: TgUser, raw: bytes, file_size: int):
    """Point the update at a document upload whose download answers *raw*."""
    document = Document(file_id="file-id", file_unique_id="unique-id", file_size=file_size, file_name="broadcast.yaml")
    message = Message(
        message_id=1,
        date=dt.datetime(2023, 1, 1, tzinfo=dt.UTC),
        chat=Chat(id=CHAT_ID, type="private"),
        from_user=tg_user,
        document=document,
    )
    telegram_file = mock.MagicMock()
    telegram_file.download_as_bytearray = mock.AsyncMock(return_value=bytearray(raw))
    app.bot.get_file = mock.AsyncMock(return_value=telegram_file)
    handler_context.update = Update(1, message=message)


# --- The chain: one broadcast_id from the draft to the send ---


@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_the_draft_id_binds_every_line_after_it_is_minted(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    caplog: pytest.LogCaptureFixture,
):
    """`create_draft` mints the id the sender's own run logs are bound to, so one `broadcast_id`
    filter has to return the operator's side of the story as well as the delivery side."""
    caplog.set_level(logging.INFO)
    register_member(user_with_settings)

    with patch_member_counts({"en": 5, "es_ES": 3}):
        await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    created = log_record(caplog, "Broadcast draft created")
    assert created.__dict__["broadcast_id"] is not None
    assert created.__dict__["author_tg_id"] == user_with_settings.tg_user_id
    assert created.__dict__["languages"] == ["en", "es_ES"]
    assert created.__dict__["char_counts"] == {"en": 5, "es_ES": 4}

    requested = log_record(caplog, "Broadcast confirmation requested")
    assert requested.__dict__["broadcast_id"] == created.__dict__["broadcast_id"]
    assert requested.__dict__["outcome"] == "awaiting_confirmation"
    assert requested.__dict__["total_recipients"] == 8


@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_the_audience_line_records_the_english_fold(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    caplog: pytest.LogCaptureFixture,
):
    """ "Why did N members get English?" is unanswerable once the users table moves on."""
    caplog.set_level(logging.INFO)
    register_member(user_with_settings)

    with patch_member_counts({"en": 5, "es_ES": 3, "de_DE": 2, "it_IT": 1}):
        await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    record = log_record(caplog, "Broadcast audience computed")
    assert record.__dict__["provided_languages"] == ["en", "es_ES"]
    assert record.__dict__["folded_into_fallback"] == 3
    assert record.__dict__["per_language"] == {"en": 8, "es_ES": 3}
    assert record.__dict__["total_recipients"] == 11


# --- The authorisation ---


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_queueing_records_who_authorised_the_send(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_user(user_with_settings)
    set_get_result(mock_session, drafted_broadcast(("en", "es_ES")))

    await call_handler(BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, handler_context=handler_context)

    record = log_record(caplog, "Broadcast queued")
    assert record.__dict__["broadcast_id"] == BROADCAST_ID
    assert record.__dict__["broadcast_name"] == BROADCAST_NAME
    assert record.__dict__["author_tg_id"] == ADMIN_TG_ID
    assert record.__dict__["user_id"] == user_with_settings.db_id
    assert record.__dict__["previous_status"] == BroadcastStatus.DRAFT.value
    assert record.__dict__["outcome"] == "queued"
    assert record.__dict__["languages"] == ["en", "es_ES"]


# Each refusal is a different operator experience, and the author mismatch is one admin acting on
# another admin's draft — a probe worth being able to count on its own.
REFUSAL_CASES: list[tuple[str, Callable[[], Broadcast | None], str]] = [
    ("missing", lambda: None, "draft_not_found"),
    ("other_author", lambda: create_broadcast(id=BROADCAST_ID, author_tg_id=999), "author_mismatch"),
    (
        "already_queued",
        lambda: create_broadcast(id=BROADCAST_ID, author_tg_id=ADMIN_TG_ID, status=BroadcastStatus.QUEUED),
        "status_not_draft",
    ),
]


@pytest.mark.parametrize(
    ("make_broadcast", "expected_reason"),
    [pytest.param(factory, reason, id=case_id) for case_id, factory, reason in REFUSAL_CASES],
)
@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_an_unusable_draft_names_which_refusal_it_hit(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
    make_broadcast: Callable[[], Broadcast | None],
    expected_reason: str,
):
    caplog.set_level(logging.INFO)
    mock_session.add_user(user_with_settings)
    set_get_result(mock_session, make_broadcast())

    await call_handler(BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK, handler_context=handler_context)

    record = log_record(caplog, "Broadcast draft not usable")
    assert record.levelname == "WARNING"
    assert record.__dict__["broadcast_id"] == BROADCAST_ID
    assert record.__dict__["reason"] == expected_reason


# --- Deleted work ---


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CANCEL_BROADCAST.with_id(BROADCAST_ID))], indirect=True
)
async def test_cancelling_records_both_the_deletion_and_the_flow_end(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_user(user_with_settings)
    set_get_result(mock_session, drafted_broadcast())

    await call_handler(BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, handler_context=handler_context)

    discarded = log_record(caplog, DRAFT_DISCARDED_EVENT)
    assert discarded.__dict__["broadcast_id"] == BROADCAST_ID
    assert discarded.__dict__["broadcast_name"] == BROADCAST_NAME
    assert discarded.__dict__["reason"] == "operator_cancelled"
    assert log_record(caplog, "Broadcast flow cancelled").__dict__["outcome"] == "draft_discarded"


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CANCEL_BROADCAST)], indirect=True)
async def test_cancelling_before_a_draft_exists_still_records_the_flow_end(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    """The entry-prompt Cancel carries no draft id, so nothing is deleted and the abandoned flow
    would otherwise leave no line at all."""
    caplog.set_level(logging.INFO)
    mock_session.add_user(user_with_settings)

    await call_handler(BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK, handler_context=handler_context)

    record = log_record(caplog, "Broadcast flow cancelled")
    assert record.__dict__["outcome"] == "no_draft"
    assert record.__dict__["reason"] == "no_draft_id_in_callback"


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.BROADCAST)], indirect=True)
async def test_reopening_the_flow_names_the_sweep_that_deleted_the_old_draft(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    register_author_drafts: RegisterAuthorDrafts,
    caplog: pytest.LogCaptureFixture,
):
    """The same sweep runs on re-entry and on a replacing upload, so the cause rides on the line."""
    caplog.set_level(logging.INFO)
    register_member(user_with_settings)
    register_author_drafts(user_with_settings.tg_user_id, (drafted_broadcast(),))

    await call_handler(BroadcastHandlerId.BROADCAST_OPEN_CALLBACK, handler_context=handler_context)

    assert log_record(caplog, DRAFT_DISCARDED_EVENT).__dict__["reason"] == "flow_reentry"
    assert log_record(caplog, "Broadcast flow opened").__dict__["outcome"] == "awaiting_content"


# --- Refused uploads ---


@pytest.mark.parametrize("update", [UpdateRequest(message_text=UNPARSEABLE_YAML)], indirect=True)
async def test_a_rejected_upload_is_named_without_quoting_the_operator_content(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    caplog: pytest.LogCaptureFixture,
):
    """The reply renders the parser detail; the log line must not, since that detail quotes the
    uploaded body back."""
    caplog.set_level(logging.INFO)
    register_member(user_with_settings)

    await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    record = log_record(caplog, "Broadcast content rejected")
    assert record.levelname == "WARNING"
    assert record.__dict__["reason"] == "error_invalid_yaml"
    assert "detail" not in record.__dict__
    assert UNPARSEABLE_FRAGMENT not in caplog.text


@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_an_operator_without_a_member_row_is_recorded_rather_than_dropped(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """The admin allowlist and the users table disagree: the upload is dropped with no reply."""
    caplog.set_level(logging.INFO)

    await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    record = log_record(caplog, UPLOAD_IGNORED_EVENT)
    assert record.levelname == "WARNING"
    assert record.__dict__["reason"] == "operator_not_member_user"


@pytest.mark.parametrize(
    ("raw", "declared_size", "expected_reason"),
    [
        pytest.param(b"x", MAX_DOCUMENT_BYTES + 1, "declared_size_over_limit", id="declared_too_large"),
        pytest.param(b"\xff\xfe\x00", 3, "not_utf8", id="undecodable"),
    ],
)
@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_a_refused_document_names_which_check_refused_it(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    app: StubMitupApp,
    user_with_settings: User,
    register_member: RegisterMember,
    caplog: pytest.LogCaptureFixture,
    raw: bytes,
    declared_size: int,
    expected_reason: str,
):
    """A `file_size` header that lies, a genuinely oversized download and a non-UTF-8 file are three
    different operator problems answered by two identical-looking replies."""
    caplog.set_level(logging.INFO)
    register_member(user_with_settings)
    assert update.effective_user is not None
    document_upload(handler_context, app, update.effective_user, raw, declared_size)

    await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    record = log_record(caplog, DOCUMENT_REJECTED_EVENT)
    assert record.levelname == "WARNING"
    assert record.__dict__["reason"] == expected_reason
    assert record.__dict__["file_name"] == "broadcast.yaml"
    assert record.__dict__["limit_bytes"] == MAX_DOCUMENT_BYTES
