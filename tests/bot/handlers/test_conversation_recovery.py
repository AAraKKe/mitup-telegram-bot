"""What a conversation leaves behind, driven through ``Application.process_update``.

Three kinds of remnant are covered here, and all need the real routing to show themselves.

A rejection raised from a conversation's message step is answered by the registry's error handling,
which leaves the user on a screen that navigates elsewhere. The conversation it interrupted must not
survive that: a live state keeps claiming the user's next messages, and the sibling conversations
that read plain text are shadowed by it.

The other remnant is a message: a flow that answers in a message of its own leaves that message, and
its buttons, in the chat after the flow is over. Tapping one has to land somewhere — a button no
handler claims reaches the registry's callback fallback, which raises rather than answering.

The third is a conversation left live by the person moving on. Every conversation except onboarding
shares the default handler group and the first match wins, so a live one claims the text meant for
whatever came after it. A person therefore has one live conversation at most, and clearing
conversation context ends it.

These tests drive a real application with the production registry bound, so routing between the
conversations is exercised as in production rather than handler-by-handler.
"""

import datetime as dt
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from typing import ClassVar, cast
from unittest import mock

import pytest
from sqlmodel import select
from telegram import Chat, Update
from telegram import User as TgUser
from telegram.ext import Application, ApplicationBuilder, ContextTypes, ConversationHandler, ExtBot

from mitup_bot import supporter
from mitup_bot.callback_data import CallbackData, DateCallbackData
from mitup_bot.config import LimitsConfig
from mitup_bot.custom_context import BOT_CONFIG_KEY, MitupContext, MitupUserData
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState as EditMeetingState
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.when import screens
from mitup_bot.handlers.meeting.enums import ConversationInviteState, ConversationMeetingState, MeetingHandlerId
from mitup_bot.handlers.registration_process.enums import RegistrationProcessHandlerId
from mitup_bot.models import MeetingCounts, Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import CommonMessages, MeetingCreationMessages, MeetingEditContentMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MessageBase
from mitup_bot.utils.rich_message import datetime_link_content
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import MockApi, UpdateRequest, create_bot_config, make_test_metrics_client
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.fixtures import create_update
from tests.helpers.logs import log_record
from tests.helpers.stub_db import MockDbSession
from tests.helpers.types import SeedMeetingCounts

# PTB keys per_chat=True/per_user=True conversation state by (chat_id, user_id); the update
# helpers default both ids.
CONVERSATION_KEY = (DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS["id"])

MENU_COUNTS = MeetingCounts(active=1, joined=0, past=0)
NEW_TIMEZONE = "Europe/Madrid"


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


def live_conversations() -> set[HandlerId]:
    """Every conversation holding a state, which in these tests can only be the acting user's."""
    return {
        handler_id
        for handler_id, wrapper in HandlersRegistry.handlers.items()
        if wrapper.is_conversation() and conversation_for(handler_id)._conversations
    }


@pytest.fixture
async def routing_app() -> AsyncGenerator[Application]:
    """A real application with the full production registry bound, initialized for process_update.

    The conversation handlers it binds are registry singletons shared with every other test; the
    autouse `clean_conversation_state` fixture is what keeps their state out of this one.
    """
    RecordingContext.created.clear()

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
    return factory.main_menu_view(RenderContext(lang=lang), message=CommonMessages.CONTEXT_LOST.rich(lang=lang))


def assert_only_reply_is_the_created_meeting(mock_session: MockDbSession, reply_to: Update, title: str, lang: str):
    created = [obj for obj in mock_session.objects_added if isinstance(obj, Meetup)]
    assert [meeting.title for meeting in created] == [title]
    success = meeting_views.owner_view(created[0]).with_context(
        MeetingCreationMessages.CREATED.rich(title=created[0].title, lang=lang)
    )
    assert views_sent_for(reply_to) == [success]


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

    assert_only_reply_is_the_created_meeting(mock_session, created_title, "Board games night", user_with_settings.lang)
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


MEETING_START = dt.datetime(2099, 7, 16, 18, 0, tzinfo=dt.UTC)


def meeting_with_start_time(mock_session: MockDbSession, user: User) -> Meetup:
    """Register the user's meeting with a start time, which either editor needs to open at all."""
    meeting = user.meetups[0]
    meeting.datetime = MEETING_START
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(meeting)
    return meeting


async def test_end_editor_button_taps_reopen_the_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The end editor's button works with no conversation live behind it.

    The beyond-horizon upsell is a message of its own, so its back button stays tappable once the
    flow has ended — by finishing, by being cancelled, or by a deploy dropping in-memory state. The
    tap has to reopen the flow: the screen it draws is answered by handlers that only exist inside
    the conversation, and unclaimed it would reach the fallback, which raises.
    """
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    conversation = conversation_for(EditMeetingHandlerId.END_EDITOR_CONVERSATION)
    assert conversation._conversations.get(CONVERSATION_KEY) is None

    tapped = await process(
        routing_app,
        UpdateRequest(callback_query=cb.REOPEN_END_EDITOR.with_id(meeting.db_id)),
        tg_chat,
        tg_user,
    )

    assert meeting.datetime is not None
    month = meeting.owner.datetime_in_tz(meeting.datetime).date()
    assert views_sent_for(tapped) == [screens.end_datetime_card(meeting, user_with_settings.lang, month=month)]
    assert conversation._conversations.get(CONVERSATION_KEY) == EditMeetingState.END_DATETIME_CARD


async def test_end_editor_button_returns_from_the_calendar_mid_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The same button taken mid-flow redraws the card instead of restarting anything.

    Reentry at the entry point resolves to the state the owner would have reached anyway, so a
    reopen while the flow is live is indistinguishable from one after it ended.
    """
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    conversation = conversation_for(EditMeetingHandlerId.END_EDITOR_CONVERSATION)

    await process(
        routing_app, UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(
        routing_app,
        UpdateRequest(callback_query=cb.NAVIGATE_END_CALENDAR.with_id(meeting.db_id).with_date(MEETING_START.date())),
        tg_chat,
        tg_user,
    )
    assert conversation._conversations.get(CONVERSATION_KEY) == EditMeetingState.END_DATETIME_CARD

    went_back = await process(
        routing_app,
        UpdateRequest(callback_query=cb.REOPEN_END_EDITOR.with_id(meeting.db_id)),
        tg_chat,
        tg_user,
    )

    assert meeting.datetime is not None
    month = meeting.owner.datetime_in_tz(meeting.datetime).date()
    assert views_sent_for(went_back) == [screens.end_datetime_card(meeting, user_with_settings.lang, month=month)]
    assert conversation._conversations.get(CONVERSATION_KEY) == EditMeetingState.END_DATETIME_CARD


async def test_start_editor_button_taps_reopen_the_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The start editor's button behaves like the end's: a tap with no live flow reopens one.

    The start half's upsell reply carries the same kind of orphaned back button, and the editor it
    draws is likewise only answerable from inside the conversation.
    """
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    conversation = conversation_for(EditMeetingHandlerId.START_EDITOR_CONVERSATION)
    assert conversation._conversations.get(CONVERSATION_KEY) is None

    tapped = await process(
        routing_app,
        UpdateRequest(callback_query=cb.REOPEN_START_EDITOR.with_id(meeting.db_id)),
        tg_chat,
        tg_user,
    )

    assert meeting.datetime is not None
    month = meeting.owner.datetime_in_tz(meeting.datetime).date()
    assert views_sent_for(tapped) == [screens.start_datetime_card(meeting, user_with_settings.lang, month=month)]
    assert conversation._conversations.get(CONVERSATION_KEY) == EditMeetingState.START_DATETIME_CARD


def invitable_meeting(mock_session: MockDbSession, user: User) -> Meetup:
    meeting = user.meetups[0]
    meeting.allow_invitation = True
    mock_session.add_object(user, "tg_user_id")
    mock_session.add_object(meeting)
    return meeting


def seed_member(mock_session: MockDbSession, seed_meeting_counts: SeedMeetingCounts, user: User):
    """Seed what a MEMBER's `/start` and the main menu read, on top of the user row already seeded."""
    user.status = UserStatus.MEMBER
    member_lookup = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
    mock_session.add_objects_with_statement(member_lookup, (user,))
    seed_meeting_counts(user, MENU_COUNTS)


DATE_EDITORS = pytest.mark.parametrize(
    ("open_editor", "editor_conversation"),
    [
        (cb.OPEN_END_EDITOR, EditMeetingHandlerId.END_EDITOR_CONVERSATION),
        (cb.OPEN_START_EDITOR, EditMeetingHandlerId.START_EDITOR_CONVERSATION),
    ],
    ids=["end_editor", "start_editor"],
)


@DATE_EDITORS
async def test_title_after_start_reaches_create_meeting_from_an_open_date_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    open_editor: CallbackData,
    editor_conversation: HandlerId,
):
    """A date editor has no `/start` fallback, so the command leaves it for the menu; the flow
    started there gets the title, and the old meeting's dates stay as they were."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    seed_member(mock_session, seed_meeting_counts, user_with_settings)

    await process(routing_app, UpdateRequest(callback_query=open_editor.with_id(meeting.db_id)), tg_chat, tg_user)
    assert live_conversations() == {editor_conversation}

    await process(routing_app, UpdateRequest(command="start"), tg_chat, tg_user)
    assert live_conversations() == set()

    await process(routing_app, UpdateRequest(callback_query=cb.CREATE_MEETING), tg_chat, tg_user)
    assert live_conversations() == {MeetingHandlerId.CREATE_MEETING_CONVERSATION}

    sent_title = await process(routing_app, UpdateRequest(message_text="Board games night"), tg_chat, tg_user)

    assert_only_reply_is_the_created_meeting(mock_session, sent_title, "Board games night", user_with_settings.lang)
    assert (meeting.datetime, meeting.end_datetime) == (MEETING_START, None)
    assert live_conversations() == set()


async def test_title_after_the_main_menu_reaches_create_meeting_from_an_open_date_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The main menu clears the editor's stored meeting, so its state cannot stay live to read it."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    seed_member(mock_session, seed_meeting_counts, user_with_settings)

    await process(
        routing_app, UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(routing_app, UpdateRequest(callback_query=cb.MAIN_MENU), tg_chat, tg_user)
    assert live_conversations() == set()

    await process(routing_app, UpdateRequest(callback_query=cb.CREATE_MEETING), tg_chat, tg_user)
    sent_title = await process(routing_app, UpdateRequest(message_text="Board games night"), tg_chat, tg_user)

    assert_only_reply_is_the_created_meeting(mock_session, sent_title, "Board games night", user_with_settings.lang)


@pytest.mark.parametrize(
    "open_editor",
    [cb.EDIT_MEETING_TITLE, cb.EDIT_MEETING_DESCRIPTION, cb.EDIT_MEETING_LOCATION_NAME],
    ids=["title", "description", "location_name"],
)
async def test_old_create_button_takes_the_text_away_from_a_text_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    open_editor: CallbackData,
):
    """Entering creation ends the editor, so the title cannot overwrite the old meeting's text."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)
    fields_before = (meeting.title, meeting.description, meeting.location.name)

    await process(routing_app, UpdateRequest(callback_query=open_editor.with_id(meeting.db_id)), tg_chat, tg_user)
    await process(routing_app, UpdateRequest(callback_query=cb.CREATE_MEETING), tg_chat, tg_user)
    assert live_conversations() == {MeetingHandlerId.CREATE_MEETING_CONVERSATION}

    sent_title = await process(routing_app, UpdateRequest(message_text="Board games night"), tg_chat, tg_user)

    assert_only_reply_is_the_created_meeting(mock_session, sent_title, "Board games night", user_with_settings.lang)
    assert (meeting.title, meeting.description, meeting.location.name) == fields_before


@pytest.mark.parametrize(
    "leave_editor",
    [cb.EDIT_MEETING, cb.DELETE_MEETING_TIMES, cb.DELETE_MEETING_END_TIME],
    ids=["edit_hub", "clear_times_prompt", "remove_end_time_prompt"],
)
async def test_leaving_the_date_editor_ends_it(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    leave_editor: CallbackData,
):
    """These screens clear the editor's stored meeting, so text sent next finds no state to claim it."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)

    await process(
        routing_app, UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(routing_app, UpdateRequest(callback_query=leave_editor.with_id(meeting.db_id)), tg_chat, tg_user)

    assert live_conversations() == set()
    sent_text = await process(routing_app, UpdateRequest(message_text="tomorrow maybe"), tg_chat, tg_user)
    assert views_sent_for(sent_text) == []


async def test_text_reaches_the_editor_entered_last(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The description editor is bound ahead of the title editor, so it would claim the text if it
    stayed live."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)
    description_before = meeting.description

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )
    assert live_conversations() == {EditMeetingHandlerId.TITLE_CONVERSATION}

    await process(routing_app, UpdateRequest(message_text="A better title"), tg_chat, tg_user)

    assert meeting.title == "A better title"
    assert meeting.description == description_before
    assert live_conversations() == set()


async def test_refused_create_meeting_leaves_the_open_editor_live(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    monkeypatch: pytest.MonkeyPatch,
):
    """Only a conversation that actually opens ends the others, so a refusal strands nobody."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_active_meetings=2))
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(routing_app, UpdateRequest(callback_query=cb.CREATE_MEETING), tg_chat, tg_user)
    assert live_conversations() == {EditMeetingHandlerId.TITLE_CONVERSATION}

    await process(routing_app, UpdateRequest(message_text="A better title"), tg_chat, tg_user)

    assert meeting.title == "A better title"
    assert [obj for obj in mock_session.objects_added if isinstance(obj, Meetup)] == []


@pytest.mark.parametrize(
    ("open_editor", "editor_conversation", "notice"),
    [
        (
            cb.EDIT_MEETING_TITLE,
            EditMeetingHandlerId.TITLE_CONVERSATION,
            MeetingEditContentMessages.TITLE_ON_EXIT,
        ),
        (
            cb.EDIT_MEETING_DESCRIPTION,
            EditMeetingHandlerId.DESCRIPTION_CONVERSATION,
            MeetingEditContentMessages.DESCRIPTION_ON_EXIT,
        ),
    ],
    ids=["title", "description"],
)
async def test_start_in_a_text_editor_shows_the_interrupted_view_and_keeps_the_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    open_editor: CallbackData,
    editor_conversation: HandlerId,
    notice: MessageBase,
):
    """The command is not read as the new text: the editor's fallback answers it instead."""
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)
    seed_member(mock_session, seed_meeting_counts, user_with_settings)
    fields_before = (meeting.title, meeting.description)

    await process(routing_app, UpdateRequest(callback_query=open_editor.with_id(meeting.db_id)), tg_chat, tg_user)
    interrupted = await process(routing_app, UpdateRequest(command="start"), tg_chat, tg_user)

    assert views_sent_for(interrupted) == [
        factory.conversation_interrupted_view(
            RenderContext(lang=user_with_settings.lang),
            notice=notice,
            cancel_callback=cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
        )
    ]
    assert live_conversations() == {editor_conversation}
    assert (meeting.title, meeting.description) == fields_before


async def test_entering_a_conversation_ends_the_invite_flow(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The invite flow is keyed by user alone, so its key differs from the conversation entered."""
    meeting = invitable_meeting(mock_session, user_with_settings)

    await process(routing_app, UpdateRequest(callback_query=cb.INVITE.with_id(meeting.db_id)), tg_chat, tg_user)
    assert live_conversations() == {MeetingHandlerId.INVITE_USERS_CONVERSATION}

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )

    assert live_conversations() == {EditMeetingHandlerId.TITLE_CONVERSATION}


@pytest.mark.parametrize(
    "tapped_card",
    [
        UpdateRequest(callback_query=cb.INVITE, from_bot_chat=False),
        UpdateRequest(callback_query=cb.INVITE),
    ],
    ids=["inline_message_without_a_chat", "group_chat"],
)
async def test_invite_from_outside_the_bot_chat_ends_the_open_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    tapped_card: UpdateRequest,
):
    """The tap comes from outside the private chat the editor lives in, and the name typed next
    still goes to the invite instead of overwriting the title."""
    meeting = invitable_meeting(mock_session, user_with_settings)
    title_before = meeting.title
    group_chat = Chat(id=-100, type=Chat.SUPERGROUP)

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(
        routing_app, replace(tapped_card, callback_query=cb.INVITE.with_id(meeting.db_id)), group_chat, tg_user
    )
    assert live_conversations() == {MeetingHandlerId.INVITE_USERS_CONVERSATION}

    await process(routing_app, UpdateRequest(message_text="My friend"), tg_chat, tg_user)

    assert meeting.title == title_before
    assert conversation_for(MeetingHandlerId.INVITE_USERS_CONVERSATION)._conversations == {
        (tg_user.id,): ConversationInviteState.CONFIRMATION
    }


async def test_inline_deep_link_ends_the_open_editor_and_forgets_its_cancel(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """The deep link stores no on-exit notice of its own, so a command in its title step lands on
    the main menu rather than offering the old editor's Cancel."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    seed_member(mock_session, seed_meeting_counts, user_with_settings)
    lang = user_with_settings.lang

    await process(
        routing_app, UpdateRequest(callback_query=cb.OPEN_END_EDITOR.with_id(meeting.db_id)), tg_chat, tg_user
    )
    deep_link = await process(routing_app, UpdateRequest(command="start", command_args="inline"), tg_chat, tg_user)

    assert views_sent_for(deep_link) == [
        factory.create_meeting_view(RenderContext(lang=lang), datetime_link=datetime_link_content())
    ]
    assert live_conversations() == {MeetingHandlerId.CREATE_MEETING_CONVERSATION}

    main_menu = await process(routing_app, UpdateRequest(command="main_menu"), tg_chat, tg_user)

    assert views_sent_for(main_menu) == [factory.main_menu_view(RenderContext(lang=lang), counts=MENU_COUNTS)]
    assert live_conversations() == set()


async def test_main_menu_during_onboarding_leaves_onboarding_live(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """An old menu button clears conversation context, which onboarding does not read, so the
    timezone sent next still completes it."""
    user_with_settings.status = UserStatus.LEFT
    mock_session.add_object(user_with_settings, "tg_user_id")
    seed_meeting_counts(user_with_settings, MENU_COUNTS)

    await process(routing_app, UpdateRequest(command="start"), tg_chat, tg_user)
    menu = await process(routing_app, UpdateRequest(callback_query=cb.MAIN_MENU), tg_chat, tg_user)

    lang = user_with_settings.lang
    assert views_sent_for(menu) == [factory.main_menu_view(RenderContext(lang=lang), counts=MENU_COUNTS)]
    assert live_conversations() == {RegistrationProcessHandlerId.TIMEZONE_CONVERSATION}

    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address", return_value=NEW_TIMEZONE):
        await process(routing_app, UpdateRequest(message_text="Madrid"), tg_chat, tg_user)

    assert user_with_settings.status is UserStatus.MEMBER
    assert user_with_settings.settings.timezone == NEW_TIMEZONE
    assert live_conversations() == set()


async def test_entering_onboarding_ends_an_open_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    seed_meeting_counts: SeedMeetingCounts,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
):
    """A member who left with an editor open comes back through `/start`, and once onboarding is
    done the editor must not be waiting to take their next message as a title."""
    meeting = user_with_settings.meetups[0]
    title_before = meeting.title
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)
    seed_meeting_counts(user_with_settings, MENU_COUNTS)

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )
    user_with_settings.status = UserStatus.LEFT

    await process(routing_app, UpdateRequest(command="start"), tg_chat, tg_user)

    assert live_conversations() == {RegistrationProcessHandlerId.TIMEZONE_CONVERSATION}

    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address", return_value=NEW_TIMEZONE):
        await process(routing_app, UpdateRequest(message_text="Madrid"), tg_chat, tg_user)

    assert live_conversations() == set()
    sent_after_onboarding = await process(routing_app, UpdateRequest(message_text="Not a title"), tg_chat, tg_user)
    assert views_sent_for(sent_after_onboarding) == []
    assert meeting.title == title_before


# Only a date past the horizon redraws the tapped card, as an upsell: a date the rules refuse is
# answered by an alert alone.
REFUSED_PICKS = pytest.mark.parametrize(
    ("pick", "day", "shows_upsell", "editor_conversation", "date_card"),
    [
        (
            cb.PICK_END_DATE,
            MEETING_START.date() - dt.timedelta(days=1),
            False,
            EditMeetingHandlerId.END_EDITOR_CONVERSATION,
            screens.end_datetime_card,
        ),
        (
            cb.PICK_END_DATE,
            MEETING_START.date() + dt.timedelta(days=1),
            True,
            EditMeetingHandlerId.END_EDITOR_CONVERSATION,
            screens.end_datetime_card,
        ),
        (
            cb.PICK_START_DATE,
            dt.date(2000, 1, 1),
            False,
            EditMeetingHandlerId.START_EDITOR_CONVERSATION,
            screens.start_datetime_card,
        ),
        (
            cb.PICK_START_DATE,
            MEETING_START.date() + dt.timedelta(days=30),
            True,
            EditMeetingHandlerId.START_EDITOR_CONVERSATION,
            screens.start_datetime_card,
        ),
    ],
    ids=["end_invalid", "end_beyond_horizon", "start_invalid", "start_beyond_horizon"],
)


async def refuse_pick_then_type(
    app: Application,
    meeting: Meetup,
    pick: DateCallbackData,
    day: dt.date,
    shows_upsell: bool,
    tg_chat: Chat,
    tg_user: TgUser,
) -> Update:
    picked = await process(
        app, UpdateRequest(callback_query=pick.with_id(meeting.db_id).with_date(day)), tg_chat, tg_user
    )
    assert bool(views_sent_for(picked)) is shows_upsell
    return await process(app, UpdateRequest(message_text="some day soon"), tg_chat, tg_user)


def date_card_asking_again(meeting: Meetup, date_card: Callable[..., MitupView], lang: str) -> MitupView:
    """The card the date editor answers with when the typed text is not a date."""
    assert meeting.datetime is not None
    return date_card(
        meeting,
        lang,
        month=meeting.owner.datetime_in_tz(meeting.datetime).date(),
        error=CommonMessages.DATETIME_INPUT_INVALID.rich(lang=lang, datetime_link=datetime_link_content()),
    )


@REFUSED_PICKS
async def test_text_after_a_refused_pick_on_an_old_card_reaches_the_date_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    pick: DateCallbackData,
    day: dt.date,
    shows_upsell: bool,
    editor_conversation: HandlerId,
    date_card: Callable[..., MitupView],
):
    """A tap on an old card revives the flow even when its date is refused, so the flow it leaves
    live has the meeting it needs."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    assert live_conversations() == set()

    typed = await refuse_pick_then_type(routing_app, meeting, pick, day, shows_upsell, tg_chat, tg_user)

    assert views_sent_for(typed) == [date_card_asking_again(meeting, date_card, user_with_settings.lang)]
    assert live_conversations() == {editor_conversation}
    assert (meeting.datetime, meeting.end_datetime) == (MEETING_START, None)


@REFUSED_PICKS
async def test_text_after_a_refused_pick_with_a_title_editor_open_reaches_the_date_editor(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    pick: DateCallbackData,
    day: dt.date,
    shows_upsell: bool,
    editor_conversation: HandlerId,
    date_card: Callable[..., MitupView],
):
    """The date editor is the flow entered last, so it gets the text and the title stays as it was."""
    meeting = meeting_with_start_time(mock_session, user_with_settings)
    title_before = meeting.title
    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )

    typed = await refuse_pick_then_type(routing_app, meeting, pick, day, shows_upsell, tg_chat, tg_user)

    assert views_sent_for(typed) == [date_card_asking_again(meeting, date_card, user_with_settings.lang)]
    assert live_conversations() == {editor_conversation}
    assert meeting.title == title_before


@pytest.mark.parametrize(
    ("next_tap", "reason"),
    [
        (cb.CREATE_MEETING, "another_conversation_entered"),
        (cb.EDIT_MEETING.with_id(1), "context_cleared"),
    ],
    ids=["another_conversation_entered", "context_cleared"],
)
async def test_ending_a_conversation_is_logged_with_what_ended_it(
    routing_app: Application,
    mock_session: MockDbSession,
    user_with_settings: User,
    tg_chat: Chat,
    tg_user: TgUser,
    caplog: pytest.LogCaptureFixture,
    next_tap: CallbackData,
    reason: str,
):
    caplog.set_level(logging.INFO)
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    await process(
        routing_app, UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(meeting.db_id)), tg_chat, tg_user
    )
    await process(routing_app, UpdateRequest(callback_query=next_tap), tg_chat, tg_user)

    ended = log_record(caplog, "Conversations ended")
    assert ended.levelname == "INFO"
    assert ended.__dict__["conversations_ended"] == [EditMeetingHandlerId.TITLE_CONVERSATION.value]
    assert ended.__dict__["reason"] == reason
