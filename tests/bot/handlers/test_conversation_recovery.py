"""What a guard rejection leaves behind, driven through ``Application.process_update``.

A rejection raised from a conversation's message step is answered by the registry's error handling,
which leaves the user on a screen that navigates elsewhere. The conversation it interrupted must not
survive that: a live state keeps claiming the user's next messages, and the sibling conversations
that read plain text are shadowed by it. These tests drive a real application with the production
registry bound, so the interaction between two conversations is exercised as in production rather
than handler-by-handler.
"""

from collections.abc import AsyncGenerator
from typing import ClassVar, cast
from unittest import mock

import pytest
from sqlmodel import select
from telegram import Chat, Update
from telegram import User as TgUser
from telegram.ext import Application, ApplicationBuilder, ContextTypes, ConversationHandler, ExtBot

from mitup_bot.custom_context import BOT_CONFIG_KEY, MitupContext, MitupUserData
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState as EditMeetingState
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.enums import ConversationMeetingState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import CommonMessages, MeetingCreationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import MockApi, UpdateRequest, create_bot_config, make_test_metrics_client
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.fixtures import create_update
from tests.helpers.stub_db import MockDbSession

# PTB keys per_chat=True/per_user=True conversation state by (chat_id, user_id); the update
# helpers default both ids.
CONVERSATION_KEY = (DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS["id"])


class RecordingContext(MitupContext):
    """Context class for process_update tests.

    ``Application.process_update`` builds one context per update via ``from_update``; injecting
    MockApi there records handler API calls instead of hitting the (mocked) bot, and keeping every
    built context lets tests assert on it afterwards.
    """

    created: ClassVar[list[MitupContext]] = []

    @classmethod
    def from_update(cls, update: object, application: Application) -> MitupContext:
        assert isinstance(update, Update)
        context = MitupContext(application, update=update, metrics=make_test_metrics_client(), api=MockApi())
        cls.created.append(context)
        return context


def conversation_for(handler_id: HandlerId) -> ConversationHandler:
    return cast(ConversationHandler, HandlersRegistry.get_handler(handler_id))


def clear_conversation_state():
    # The conversation handlers are registry singletons shared with every other test in the
    # process; drop any state keyed under the default (chat, user) so tests stay independent.
    conversation_for(EditMeetingHandlerId.TITLE_CONVERSATION)._conversations.clear()
    conversation_for(MeetingHandlerId.CREATE_MEETING_CONVERSATION)._conversations.clear()


@pytest.fixture
async def routing_app() -> AsyncGenerator[Application]:
    """A real application with the full production registry bound, initialized for process_update."""
    RecordingContext.created.clear()
    clear_conversation_state()

    builder = ApplicationBuilder()
    # Same bot setup as create_test_app: a spec'd mock with no defaults so no scheduler config leaks.
    bot = mock.MagicMock(spec=ExtBot)
    bot.defaults = None
    builder.bot(bot)
    builder.context_types(ContextTypes(context=RecordingContext, user_data=MitupUserData))
    app = builder.build()
    # Mirror create_test_app / MitupRuntime: stash a BotConfig so `context.bot_config`
    # (used by guards.is_admin) resolves. Empty allowlist keeps the acting user a non-admin.
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([])

    HandlersRegistry.bind(app)
    await app.initialize()
    try:
        yield app
    finally:
        await app.shutdown()
        clear_conversation_state()


async def process(app: Application, request: UpdateRequest, tg_chat: Chat, tg_user: TgUser) -> Update:
    """Feed an update built from `request` through the full handler-group chain and return it.

    Screens are asserted through `views_sent_for`, which finds the update's own context: an update
    that no handler matches gets no context at all, and that is a legitimate outcome here.
    """
    update = create_update(request, tg_chat, tg_user)
    if update.effective_message is not None:
        update.effective_message.set_bot(app.bot)
    await app.process_update(update)
    return update


def views_sent_for(update: Update) -> list[MitupView]:
    """Every view delivered while handling `update`, as a fresh reply or in place of a tapped screen.

    Empty when no handler matched the update: PTB builds the context only once a handler runs.
    """
    views: list[MitupView] = []
    for context in RecordingContext.created:
        if context.telegram_update is not update:
            continue
        api = cast(MockApi, context.api)
        calls = api.call_args_list("send_message") + api.call_args_list("edit_message")
        views.extend(call.kwargs["view"] for call in calls)
    return views


def forget_meeting(mock_session: MockDbSession, meeting: Meetup):
    """Make the meeting stop resolving, as it does once somebody deletes it between two updates."""
    statement = select(Meetup).where(Meetup.id == meeting.db_id)
    del mock_session.statements_registry[str(statement.compile(compile_kwargs={"literal_binds": True}))]


def context_lost_view(lang: str) -> MitupView:
    """The screen a handler produces when it reaches for conversation data that is no longer there."""
    return factory.main_menu_view(RenderContext(lang=lang), message=CommonMessages.CONTEXT_LOST.get(lang=lang))


async def start_title_edit_then_lose_the_meeting(
    app: Application, mock_session: MockDbSession, meeting: Meetup, tg_chat: Chat, tg_user: TgUser
) -> Update:
    """Take the user into the edit-title step, delete the meeting under them, and send the title."""
    await process(app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user)
    assert conversation_for(EditMeetingHandlerId.TITLE_CONVERSATION)._conversations.get(CONVERSATION_KEY) == (
        EditMeetingState.EDIT_TITLE
    )

    forget_meeting(mock_session, meeting)
    return await process(app, UpdateRequest(message_text="A better title"), tg_chat, tg_user)


async def test_rejected_title_step_ends_the_edit_conversation(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """A meeting deleted mid-edit rejects the title the user sends, and the conversation ends with it."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    rejected = await start_title_edit_then_lose_the_meeting(routing_app, mock_session, meeting, tg_chat, tg_user)

    # A message update carries no screen of ours to replace, so the rejection arrives as a reply.
    assert views_sent_for(rejected) == [factory.deleted_meeting_view(RenderContext(lang=user_with_settings.lang))]
    assert conversation_for(EditMeetingHandlerId.TITLE_CONVERSATION)._conversations.get(CONVERSATION_KEY) is None


async def test_title_after_rejection_reaches_the_create_meeting_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The flow the user starts after the rejection receives their title: the dead edit conversation
    no longer claims the message ahead of it, so no meeting-title text is stranded."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    await start_title_edit_then_lose_the_meeting(routing_app, mock_session, meeting, tg_chat, tg_user)

    await process(routing_app, UpdateRequest(callback_query=cb.CREATE_MEETING), tg_chat, tg_user)
    create_meeting = conversation_for(MeetingHandlerId.CREATE_MEETING_CONVERSATION)
    assert create_meeting._conversations.get(CONVERSATION_KEY) == ConversationMeetingState.TITLE

    created_title = await process(routing_app, UpdateRequest(message_text="Board games night"), tg_chat, tg_user)

    created = [obj for obj in mock_session.objects_added if isinstance(obj, Meetup)]
    assert len(created) == 1
    assert created[0].title == "Board games night"
    success = meeting_views.edit_view(created[0]).with_context(
        MeetingCreationMessages.SUCCESS.get(title=created[0].title, lang=user_with_settings.lang)
    )
    assert views_sent_for(created_title) == [success]
    assert create_meeting._conversations.get(CONVERSATION_KEY) is None


async def test_second_title_after_rejection_never_shows_the_context_lost_screen(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """Typing another title straight after the rejection is not claimed by the ended conversation,
    whose stored meeting id the rejection already cleaned away."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    await start_title_edit_then_lose_the_meeting(routing_app, mock_session, meeting, tg_chat, tg_user)

    second_title = await process(routing_app, UpdateRequest(message_text="Another title"), tg_chat, tg_user)

    assert context_lost_view(user_with_settings.lang) not in views_sent_for(second_title)
