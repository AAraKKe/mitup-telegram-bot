from unittest import mock

import pytest
from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.handlers.stale_cancel import StaleCancelHandlerId
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CommonMessages
from tests.helpers import HandlerContext, MockDbSession, UpdateRequest, call_handler
from tests.helpers.api import MockApi

# All cancel-intent callbacks that the stale cancel handler must intercept.
# Add a new entry here when a new cancel callback is introduced.
ALL_CANCEL_CALLBACKS: list[CallbackData] = [
    cb.CANCEL_CREATE_MEETING,  # cancel;meeting:
    cb.CANCEL_EDIT_MEETING_PARTICIPANS,  # cancel;meet_part:
    cb.CANCEL_EDIT_MEETING_LOCATION,  # cancel;meet_loc:
    cb.CANCEL_EDIT_MEETING_DURATION,  # cancel;meet_dur:
    cb.EDIT_MEETING_CANCEL,  # cancel;meet_edit:
    cb.CANCEL_EDIT_START_TIME,  # cancel;meet_st:
    cb.CANCEL_SETTINGS,  # cancel;settings:
    cb.CANCEL_INVITE_USER,  # cancel;invite:
]


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_CREATE_MEETING.with_id(1))],
    indirect=True,
)
async def test_stale_cancel_answers_callback_with_show_alert(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings,
    handler_context: HandlerContext,
):
    """answer_callback_query is called with show_alert=True and the STALE_CANCEL_BUTTON message."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(StaleCancelHandlerId.STALE_CANCEL_CALLBACK, handler_context=handler_context)

    expected_text = CommonMessages.STALE_CANCEL_ALERT.get(lang=user_with_settings.lang)
    # show_alert=True so the user sees a popup, not just a toast
    context.api.assert_answer_callback_query_called(update, text=expected_text, show_alert=True)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_CREATE_MEETING.with_id(1))],
    indirect=True,
)
async def test_stale_cancel_removes_inline_keyboard(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings,
    handler_context: HandlerContext,
):
    """clear_reply_markup is called to remove the inline keyboard after a stale cancel."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(StaleCancelHandlerId.STALE_CANCEL_CALLBACK, handler_context=handler_context)

    context.api.assert_method_just_called("clear_reply_markup")


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(5))],
    indirect=True,
)
async def test_stale_cancel_matches_any_cancel_action_callback(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings,
    handler_context: HandlerContext,
):
    """The handler matches cancel callbacks from different entities (meet_edit, meeting, etc.)."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(StaleCancelHandlerId.STALE_CANCEL_CALLBACK, handler_context=handler_context)

    expected_text = CommonMessages.STALE_CANCEL_ALERT.get(lang=user_with_settings.lang)
    context.api.assert_answer_callback_query_called(update, text=expected_text, show_alert=True)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_CREATE_MEETING.with_id(1))],
    indirect=True,
)
async def test_stale_cancel_answer_callback_query_called_even_when_clear_markup_raises(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings,
    handler_context: HandlerContext,
):
    """answer_callback_query runs before clear_reply_markup, so its alert is sent even when markup removal fails."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    raising_clear = mock.AsyncMock(side_effect=Exception("boom"))
    with mock.patch.object(MockApi, "clear_reply_markup", raising_clear):
        context, _ = await call_handler(StaleCancelHandlerId.STALE_CANCEL_CALLBACK, handler_context=handler_context)

    # The alert must have been sent before clear_reply_markup was attempted
    expected_text = CommonMessages.STALE_CANCEL_ALERT.get(lang=user_with_settings.lang)
    context.api.assert_answer_callback_query_called(update, text=expected_text, show_alert=True)


@pytest.mark.parametrize(
    "update",
    [
        pytest.param(UpdateRequest(callback_query=cb_constant.with_id(1)), id=str(cb_constant.with_id(1)))
        for cb_constant in ALL_CANCEL_CALLBACKS
    ],
    indirect=True,
)
async def test_stale_cancel_fires_for_all_cancel_callbacks(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings,
    handler_context: HandlerContext,
):
    """Every cancel-intent callback triggers the alert and removes the inline keyboard."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(StaleCancelHandlerId.STALE_CANCEL_CALLBACK, handler_context=handler_context)

    expected_text = CommonMessages.STALE_CANCEL_ALERT.get(lang=user_with_settings.lang)
    context.api.assert_answer_callback_query_called(update, text=expected_text, show_alert=True)
    context.api.assert_method_just_called("clear_reply_markup")
