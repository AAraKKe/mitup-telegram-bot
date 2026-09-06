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
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView, RenderContext, factory
from tests.helpers import HandlerContext, UpdateRequest, call_handler, log_record
from tests.helpers.stub_db import MockDbSession


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
async def test_export_attaches_the_user_data_to_the_privacy_screen(
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

    edited = context.api.call_args("edit_message").kwargs
    assert edited["update"] is update
    view = edited["view"]
    assert view.document is not None
    assert view.document.filename == f"mitup-export-{dt.datetime.now(dt.UTC):%Y-%m-%d}.json"
    export = json.loads(view.document.content)
    assert export["user"]["telegram_user_id"] == user_with_settings.tg_user_id
    assert [meeting["title"] for meeting in export["meetings"]] == ["Test Meeting 1", "Test Meeting 2"]
    assert view == factory.privacy_view(RenderContext(lang=user_with_settings.lang), export=view.document)
    assert PrivacyMessages.EXPORT_ATTACHED.text(lang=user_with_settings.lang) in view.message.text
    context.api.assert_send_message_not_called()
    # structlog event string is the LogRecord message; the fields ride along as record attributes.
    # The document is never retained, so this line is the only evidence of what was disclosed.
    export_record = log_record(caplog, "User data export sent")
    assert export_record.__dict__["user_id"] == user_with_settings.db_id
    assert export_record.__dict__["owned_meetings"] == 2
    assert export_record.__dict__["joined_meetings"] == 0
    assert export_record.__dict__["has_patreon"] is False
    assert export_record.__dict__["document_bytes"] == len(view.document.content)
    assert export_record.__dict__["export_filename"] == view.document.filename


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
        message=PrivacyMessages.DELETE_WARNING.rich(lang=user_with_settings.lang),
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
        message=PrivacyMessages.DELETE_LAST_CHANCE.rich(lang=user_with_settings.lang),
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
    expected_view = MitupView(message=PrivacyMessages.DELETION_MARKED.rich(lang=user_with_settings.lang), menu=[])
    context.api.assert_edit_message_called(update, expected_view)
    # These rows are hard-deleted later, so the line is the only lasting record of the blast radius.
    deletion_record = log_record(caplog, "Data deletion requested")
    assert deletion_record.__dict__["user_id"] == user_with_settings.db_id
    assert deletion_record.__dict__["prior_status"] == UserStatus.MEMBER.value
    assert deletion_record.__dict__["owned_meetings"] == len(user_with_settings.meetups)
    assert deletion_record.__dict__["joined_meetings"] == len(user_with_settings.joined_links)
    assert deletion_record.__dict__["has_patreon_link"] is False
    assert deletion_record.__dict__["reason"] == "user_confirmed_final"


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
