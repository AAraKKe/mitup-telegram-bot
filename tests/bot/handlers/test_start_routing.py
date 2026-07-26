"""End-to-end /start routing through ``Application.process_update``.

The re-onboarding conversation binds in REGISTRATION_HANDLERS_GROUP (-1), before the group-0
handlers. Its entry point checks ``guards.member_user``: MEMBERs return END silently so the update
falls through to the group-0 /start handler (main menu or the "inline" create-meeting deep link),
while everyone else is claimed via ``ApplicationHandlerStop`` so group 0 never runs. These tests
drive a real application with the production registry bound, so the group -1 / group 0 interaction
is exercised exactly as in production rather than handler-by-handler.
"""

from collections.abc import AsyncGenerator
from typing import ClassVar, cast
from unittest import mock

import pytest
from sqlmodel import select
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, ConversationHandler, ExtBot

from mitup_bot.custom_context import BOT_CONFIG_KEY, MitupContext, MitupUserData
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.meeting.enums import ConversationMeetingState, MeetingHandlerId
from mitup_bot.handlers.registration_process.enums import (
    ConversationRegistrationProcessState,
    RegistrationProcessHandlerId,
)
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import PrivacyMessages, RegistrationMessages
from mitup_bot.utils.entities import build_datetime_link
from mitup_bot.views import RenderContext
from mitup_bot.views.factory import create_meeting_view, main_menu_view
from tests.helpers import MockApi, UpdateRequest, create_bot_config, create_user, make_test_metrics_client
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.stub_db import MockDbSession

# PTB keys per_chat=True/per_user=True conversation state by (chat_id, user_id); the update
# fixture defaults both ids.
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


async def process_update(app: Application, update: Update) -> MockApi:
    """Feed the update through the full handler-group chain and return the recording api."""
    # CommandHandler.check_update resolves the command's @mention through message.get_bot(),
    # so the update must be bound to the application's bot (as PTB does when de-serializing).
    if update.effective_message is not None:
        update.effective_message.set_bot(app.bot)
    await app.process_update(update)
    assert RecordingContext.created, "No handler matched the update"
    return cast(MockApi, RecordingContext.created[-1].api)


def register_member(mock_session: MockDbSession, user: User):
    """Seed both lookups a MEMBER /start triggers: guards.member_user and User.by_tg_user_id."""
    mock_session.add_user(user)
    member_lookup = select(User).where(
        User.tg_user_id == user.tg_user_id,
        User.status == UserStatus.MEMBER,
    )
    mock_session.add_objects_with_statement(member_lookup, (user,))


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_member_start_falls_through_to_main_menu(
    routing_app: Application,
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    """A MEMBER's /start is released by the group -1 entry (silent END) and reaches group 0."""
    user_with_settings.status = UserStatus.MEMBER
    register_member(mock_session, user_with_settings)

    api = await process_update(routing_app, update)

    # times=1 asserts the main menu was the ONLY message: the re-onboarding prompt never fired.
    api.assert_send_message_called(update, main_menu_view(RenderContext(lang=user_with_settings.lang)))
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None


@pytest.mark.parametrize(
    "status",
    [UserStatus.JOINED_ONLY, UserStatus.LEFT],
    ids=["joined_only", "left"],
)
@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_non_member_start_is_claimed_by_reonboarding(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
    status: UserStatus,
):
    """A JOINED_ONLY or LEFT user's /start is claimed in group -1: the timezone prompt is sent,
    the conversation enters TIMEZONE, and the group-0 /start handler never runs."""
    existing_user = create_user(id=1, tg_user_id=123, status=status)
    mock_session.add_object(existing_user, "tg_user_id")

    api = await process_update(routing_app, update)

    # times=1: the prompt was the only message — no main menu means group 0 was stopped.
    api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_PROMPT.get(first_name=existing_user.first_name),
    )
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) == ConversationRegistrationProcessState.TIMEZONE
    # The existing row is reused; no new User row is created for re-onboarding.
    mock_session.assert_not_added()


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_pending_deletion_start_is_rejected_without_reonboarding(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    """A /start during the deletion window gets the pending-deletion notice: the row is not reused
    or promoted (no undo), no conversation starts, and the group-0 /start handler never runs."""
    marked_user = create_user(id=1, tg_user_id=123, status=UserStatus.DELETION_REQUESTED)
    mock_session.add_object(marked_user, "tg_user_id")

    api = await process_update(routing_app, update)

    # times=1: the notice was the only message — no timezone prompt and no main menu.
    api.assert_send_message_called(update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang=marked_user.lang))
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None
    assert marked_user.status is UserStatus.DELETION_REQUESTED
    mock_session.assert_not_added()


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_brand_new_user_start_is_claimed_by_reonboarding(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    """A /start from a user with no row at all is claimed in group -1 with a freshly created row."""
    api = await process_update(routing_app, update)

    api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_PROMPT.get(first_name=DEFAULT_TG_USER_PARAMS["first_name"]),
    )
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) == ConversationRegistrationProcessState.TIMEZONE

    added_users = [obj for obj in mock_session.objects_added if isinstance(obj, User)]
    assert len(added_users) == 1
    assert added_users[0].tg_user_id == DEFAULT_TG_USER_PARAMS["id"]


@pytest.mark.parametrize("update", [UpdateRequest(command="start", command_args="inline")], indirect=True)
async def test_member_start_inline_deep_link_enters_create_meeting(
    routing_app: Application,
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    """A MEMBER's "/start inline" deep link falls through group -1 and enters the group-0
    create-meeting conversation at TITLE."""
    user_with_settings.status = UserStatus.MEMBER
    register_member(mock_session, user_with_settings)

    api = await process_update(routing_app, update)

    api.assert_send_message_called(
        update, create_meeting_view(RenderContext(lang=user_with_settings.lang), datetime_link=build_datetime_link())
    )
    create_meeting = conversation_for(MeetingHandlerId.CREATE_MEETING_CONVERSATION)
    assert create_meeting._conversations.get(CONVERSATION_KEY) == ConversationMeetingState.TITLE
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None
