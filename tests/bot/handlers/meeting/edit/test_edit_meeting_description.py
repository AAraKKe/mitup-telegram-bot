import logging
import re
from collections.abc import Callable
from typing import cast

import pytest
from telegram import CallbackQuery, MessageEntity, Update
from telegram.ext import ConversationHandler

from mitup_bot import limits
from mitup_bot.custom_context import ContextId
from mitup_bot.exceptions import MalformedCallbackData, MeetingGoneError
from mitup_bot.handlers.meeting.edit.edit_meeting_description import (
    callback_query_edit_meeting_description,
    edit_description_prompt_view,
    edit_description_rich_message_handler,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.utils import CommonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingDisplayMessages, MeetingEditContentMessages
from mitup_bot.utils.rich_message import MAX_RICH_TEXT_LENGTH
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import description_content
from mitup_bot.views.mitup_view import MitupView
from tests.helpers import (
    HandlerContext,
    MetricAssertions,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    log_record,
)
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize(
    "update, expected_description",
    [
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(1)),
            lambda lang: "What a cool description. Congratulations",
        ),
        (
            UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(2)),
            lambda lang: MeetingDisplayMessages.DESCRIPTION_EMPTY.rich(lang=lang),
        ),
    ],
    ids=["meeting_with_a_previous_description", "meeting_without_a_previous_description"],
    indirect=["update"],
)
async def test_callback_query_edit_meeting_description_works(
    mock_session: MockDbSession,
    update: Update,
    expected_description: Callable[[str], str],
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    callback_query = cast(CallbackQuery, update.callback_query)
    meeting_id = (callback_query.data or "").split(":")[1]

    mock_session.add_object(user_with_settings.meetups[int(meeting_id) - 1])

    context, result = await call_handler(EditMeetingHandlerId.DESCRIPTION_CALLBACK, handler_context=handler_context)

    assert context.user_data is not None
    assert context.has_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION)

    meeting_id = context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id

    view = MitupView(
        message=MeetingEditContentMessages.DESCRIPTION_PROMPT.rich(
            lang=user_with_settings.lang, description=expected_description(user_with_settings.lang)
        ),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=user_with_settings.lang),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(cast(int, meeting_id)),
                )
            ]
        ],
    )

    context.api.assert_edit_message_called(update, view)
    assert result == ConversationMeetingState.EDIT_DESCRIPTION


async def test_callback_query_edit_meeting_description_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:")
    assert match is not None

    context.matches = [match]

    with pytest.raises(MalformedCallbackData):
        await callback_query_edit_meeting_description(update, context)


async def test_edit_meeting_description_stops_for_meeting_that_is_gone(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    meeting: Meetup,
):
    match = re.match(cb.EDIT_MEETING_DESCRIPTION.pattern, "edit;meet_desc:123")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")

    with pytest.raises(MeetingGoneError) as raised:
        await callback_query_edit_meeting_description(update, context)

    assert "User tried 'Edit description' with a meeting that does not exist." in str(raised.value)
    assert " Meeting id: 123, user id: 1" in str(raised.value)
    context.api.assert_edit_message_not_called()


async def test_edit_description_rich_message_reprompts_and_keeps_state(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    assert context.user_data is not None
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting, "id")
    context.store_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION, 1)

    state = await edit_description_rich_message_handler(update, context)

    expected = edit_description_prompt_view(meeting, user_with_settings.lang).with_context(
        CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.rich(lang=user_with_settings.lang)
    )
    context.api.assert_send_message_called(update, expected)
    assert state == ConversationMeetingState.EDIT_DESCRIPTION
    assert context.user_data.registry[ContextId.EDIT_MEETING_DESCRIPTION].meeting_id == 1
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.RICH_MESSAGE)})


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(
            message_text="Bring snacks & drinks",
            entities=[MessageEntity(type=MessageEntity.ITALIC, offset=6, length=6)],
        )
    ],
    indirect=True,
)
async def test_edit_description_message_stores_tagged_description_and_renders_rich_success(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
    metrics_client: MetricsClient,
):
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DESCRIPTION_MESSAGE,
        handler_context=HandlerContext(update=update, app=app, metrics_client=metrics_client),
        with_meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: meeting.db_id},
    )

    assert meeting.description == "Bring <i>snacks</i> &amp; drinks"
    assert meeting.plain_description == "Bring snacks & drinks"

    context.api.assert_send_message_called(update, meeting_views.owner_view(meeting))
    assert state == ConversationHandler.END


# ---------------------------------------------------------------------------
# DESCRIPTION_MESSAGE — the description character cap
# ---------------------------------------------------------------------------


OVER_CAP_DESCRIPTION = "d" * (limits.DESCRIPTION_MAX_CHARS + 1)


@pytest.mark.parametrize("update", [UpdateRequest(message_text=OVER_CAP_DESCRIPTION)], indirect=True)
async def test_over_cap_description_leaves_the_meeting_untouched_and_reprompts(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """A description past the cap never reaches the meeting, and the prompt comes back with the
    error on top so the buttons stay reachable."""
    caplog.set_level(logging.INFO)
    meeting = user_with_settings.meetups[0]
    stored_description = meeting.description
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DESCRIPTION_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: meeting.db_id},
    )

    assert meeting.description == stored_description
    assert state == ConversationMeetingState.EDIT_DESCRIPTION
    # The meeting id survives the refusal, so a shorter retry edits this same meeting.
    assert context.has_meeting_id(ContextId.EDIT_MEETING_DESCRIPTION)
    context.api.assert_method_just_called("update_meeting_messages", times=0)

    error = MeetingEditContentMessages.DESCRIPTION_TOO_LONG.rich(
        lang=user_with_settings.lang, length=len(OVER_CAP_DESCRIPTION), limit=limits.DESCRIPTION_MAX_CHARS
    )
    assert str(len(OVER_CAP_DESCRIPTION)) in error.text
    assert str(limits.DESCRIPTION_MAX_CHARS) in error.text
    assert "${" not in error.text
    context.api.assert_send_message_called(
        update, edit_description_prompt_view(meeting, user_with_settings.lang, error=error)
    )

    # The shared line is pinned in full by the title test; this only proves it names this field.
    record = log_record(caplog, "Meeting edit input rejected")
    assert record.__dict__["field"] == "description"
    assert record.__dict__["reason"] == "too_long"


@pytest.mark.parametrize("update", [UpdateRequest(message_text="d" * limits.DESCRIPTION_MAX_CHARS)], indirect=True)
async def test_description_at_the_cap_is_stored(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """The cap is inclusive: a description of exactly the maximum length is a normal edit.

    The confirmation echoes the new description above a card that already contains it, so the
    longest storable description is also the case where the two together must still fit one
    Telegram message — otherwise the edit commits and the owner never sees it land.
    """
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DESCRIPTION_MESSAGE,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: meeting.db_id},
    )

    assert meeting.description == "d" * limits.DESCRIPTION_MAX_CHARS
    assert state == ConversationHandler.END

    confirmation = cast(MitupView, context.api.call_args("send_message").kwargs["view"])
    # Nothing gives way: the echo is carried whole and the card follows it, well inside the ceiling
    # a client folds behind "Show more".
    assert confirmation.message.text_length <= MAX_RICH_TEXT_LENGTH
    assert confirmation.message.html.endswith(meeting_views.owner_view(meeting).message.html)


# ---------------------------------------------------------------------------
# Remove description: confirmation, confirm, decline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DELETE_MEETING_DESCRIPTION.with_id(1))],
    indirect=True,
)
async def test_remove_description_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.description = "An old description"
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(EditMeetingHandlerId.REMOVE_DESCRIPTION_CALLBACK, handler_context=handler_context)

    current = description_content(meeting)
    assert current is not None
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingEditContentMessages.REMOVE_DESCRIPTION_CONFIRMATION.rich(
                lang=user_with_settings.lang, description=current
            ),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING_DESCRIPTION.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING_DESCRIPTION.with_id(1),
        ),
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_DESCRIPTION.with_id(1))],
    indirect=True,
)
async def test_confirm_remove_description_clears_it_and_returns_to_the_editor(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.description = "An old description"
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_DESCRIPTION_CALLBACK, handler_context=handler_context
    )

    assert meeting.description is None

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_DESCRIPTION.with_id(1))],
    indirect=True,
)
async def test_decline_remove_description_keeps_it(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = user_with_settings.meetups[0]
    meeting.description = "An old description"
    mock_session.add_object(meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_DESCRIPTION_CALLBACK, handler_context=handler_context
    )

    assert meeting.description == "An old description"
    mock_session.assert_not_added()
    mock_session.assert_not_flushed()

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
