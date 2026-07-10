import datetime as dt
import json
import logging

import pytest
from telegram import Update

from mitup_bot.handlers.privacy import data_export
from mitup_bot.handlers.privacy.enums import PrivacyHandlerId
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, PrivacyMessages
from mitup_bot.views import ButtonConfig, MitupView, RenderContext, factory
from tests.helpers import HandlerContext, UpdateRequest, call_handler
from tests.helpers.stub_db import MockDbSession


def privacy_log_record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    """The captured record for `event`; INFO capture also picks up unrelated framework lines,
    so the lookup filters by the structlog event string (the LogRecord message)."""
    return next(record for record in caplog.records if record.message == event)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_PRIVACY)], indirect=True)
async def test_show_privacy_renders_the_privacy_screen(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.SHOW, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.privacy_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EXPORT_USER_DATA)], indirect=True)
async def test_export_sends_the_user_data_as_a_json_document(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_objects_with_statement(
        data_export.owned_meetings_statement(user_with_settings), tuple(user_with_settings.meetups)
    )

    context, _ = await call_handler(PrivacyHandlerId.EXPORT_DATA, handler_context=handler_context)

    sent = context.api.call_args("send_document").kwargs
    assert sent["update"] is update
    view = sent["view"]
    assert view.description == PrivacyMessages.EXPORT_CAPTION.get(lang=user_with_settings.lang)
    assert view.document is not None
    assert view.document.filename == f"mitup-export-{dt.datetime.now(dt.UTC):%Y-%m-%d}.json"
    export = json.loads(view.document.content)
    assert export["user"]["telegram_user_id"] == user_with_settings.tg_user_id
    assert [meeting["title"] for meeting in export["meetings"]] == ["Test Meeting 1", "Test Meeting 2"]
    # The document carries a Privacy button so it is not a dead end (plain label, no « decoration).
    assert view.keyboard == [
        [ButtonConfig(text=ButtonMessages.PRIVACY.get(lang=user_with_settings.lang), callback_data=cb.SEND_PRIVACY)]
    ]
    # The document is a new message: the privacy screen above keeps its buttons untouched.
    context.api.assert_edit_message_not_called()
    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    export_record = privacy_log_record(caplog, "User data export sent")
    assert export_record.__dict__["user_id"] == user_with_settings.db_id


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SEND_PRIVACY)], indirect=True)
async def test_send_privacy_sends_a_new_privacy_message(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    # The button lives on the export document, which must stay in the chat with its button intact,
    # so the handler posts a fresh privacy screen instead of editing the tapped message.
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.SEND_PRIVACY, handler_context=handler_context)

    context.api.assert_send_message_called(update, factory.privacy_view(RenderContext(lang=user_with_settings.lang)))
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_USER_DATA)], indirect=True)
async def test_delete_data_shows_the_consequences_warning(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.DELETE_DATA, handler_context=handler_context)

    expected_view = factory.confirmation_view(
        RenderContext(lang=user_with_settings.lang),
        message=PrivacyMessages.DELETE_WARNING.get(lang=user_with_settings.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    context.api.assert_edit_message_called(update, expected_view)
    assert user_with_settings.status is UserStatus.MEMBER


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA)], indirect=True)
async def test_first_confirmation_shows_the_last_chance_prompt(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.CONFIRM_DELETE_DATA, handler_context=handler_context)

    expected_view = factory.confirmation_view(
        RenderContext(lang=user_with_settings.lang),
        message=PrivacyMessages.DELETE_LAST_CHANCE.get(lang=user_with_settings.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    context.api.assert_edit_message_called(update, expected_view)
    # Nothing is marked until the final confirmation.
    assert user_with_settings.status is UserStatus.MEMBER


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA_FINAL)], indirect=True)
async def test_final_confirmation_marks_the_user_for_deletion(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert user_with_settings.status is UserStatus.MEMBER

    context, _ = await call_handler(PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL, handler_context=handler_context)

    assert user_with_settings.status is UserStatus.DELETION_REQUESTED
    expected_view = MitupView(
        description=PrivacyMessages.DELETION_MARKED.get(lang=user_with_settings.lang), keyboard=[]
    )
    context.api.assert_edit_message_called(update, expected_view)
    deletion_record = privacy_log_record(caplog, "Data deletion request confirmed")
    assert deletion_record.__dict__["user_id"] == user_with_settings.db_id


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_USER_DATA)], indirect=True)
async def test_decline_returns_to_the_privacy_screen_without_marking(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.DECLINE_DELETE_DATA, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.privacy_view(RenderContext(lang=user_with_settings.lang)))
    assert user_with_settings.status is UserStatus.MEMBER
