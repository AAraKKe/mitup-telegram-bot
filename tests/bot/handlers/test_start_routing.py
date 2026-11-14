"""End-to-end /start routing through ``Application.process_update``.

The re-onboarding conversation binds in REGISTRATION_HANDLERS_GROUP (-1), before the group-0
handlers. Its entry point checks ``guards.member_user``: MEMBERs return END silently so the update
falls through to the group-0 /start handler (main menu or the "inline" create-meeting deep link),
while everyone else is claimed via ``ApplicationHandlerStop`` so group 0 never runs. These tests
drive a real application with the production registry bound, so the group -1 / group 0 interaction
is exercised exactly as in production rather than handler-by-handler.
"""

from collections.abc import AsyncGenerator, Iterator
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
from mitup_bot.models import MeetingCounts, User
from mitup_bot.models.users import UserStatus
from mitup_bot.patreon.pairing import PAIRING_DEEP_LINK_PREFIX
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import CommonMessages, PrivacyMessages, RegistrationMessages
from mitup_bot.utils.rich_message import RichContent, datetime_link_content
from mitup_bot.views import RenderContext
from mitup_bot.views.collaborate import patreon_link_needs_setup_view
from mitup_bot.views.factory import create_meeting_view, main_menu_view
from tests.helpers import MockApi, UpdateRequest, create_bot_config, create_user, make_test_metrics_client
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_TG_USER_PARAMS
from tests.helpers.fixtures import create_update
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
def get_timezone_from_api() -> Iterator[mock.MagicMock]:
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


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


def register_member_lookup(mock_session: MockDbSession, user: User):
    member_lookup = select(User).where(
        User.tg_user_id == user.tg_user_id,
        User.status == UserStatus.MEMBER,
    )
    mock_session.add_objects_with_statement(member_lookup, (user,))


def register_member(mock_session: MockDbSession, user: User):
    """Seed both lookups a MEMBER /start triggers: guards.member_user and User.by_tg_user_id."""
    mock_session.add_user(user)
    register_member_lookup(mock_session, user)


def store_created_user(mock_session: MockDbSession) -> User:
    """Make the row /start created visible to later updates, as the database would.

    guards.member_user filters on status, so its lookup finds the row only when it is a MEMBER.
    """
    created_user = next(obj for obj in mock_session.objects_added if isinstance(obj, User))
    mock_session.add_user(created_user)
    if created_user.status is UserStatus.MEMBER:
        register_member_lookup(mock_session, created_user)
    return created_user


def assert_still_onboarding(api: MockApi, update: Update, user: User):
    """The prompt must be the only message sent, which rules out the main menu."""
    api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_PROMPT.rich(first_name=user.first_name, lang=user.lang),
    )
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) == ConversationRegistrationProcessState.TIMEZONE
    assert user.status is UserStatus.ONBOARDING
    assert user.member_time is None


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(command="start"), UpdateRequest(command="start", command_args="inlinemenu")],
    indirect=True,
    ids=["bare_start", "refused_share_link"],
)
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
    api.assert_send_message_called(
        update, main_menu_view(RenderContext(lang=user_with_settings.lang), counts=MeetingCounts(0, 0, 0))
    )
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None


@pytest.mark.parametrize(
    "status",
    [UserStatus.ONBOARDING, UserStatus.JOINED_ONLY, UserStatus.LEFT],
    ids=["onboarding", "joined_only", "left"],
)
@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_non_member_start_is_claimed_by_reonboarding(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
    status: UserStatus,
):
    """A non-member's /start is claimed in group -1: the timezone prompt is sent,
    the conversation enters TIMEZONE, and the group-0 /start handler never runs."""
    existing_user = create_user(id=1, tg_user_id=123, status=status)
    mock_session.add_object(existing_user, "tg_user_id")

    api = await process_update(routing_app, update)

    # times=1: the prompt was the only message — no main menu means group 0 was stopped.
    api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_PROMPT.rich(first_name=existing_user.first_name),
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
    api.assert_send_message_called(update, PrivacyMessages.PENDING_DELETION_ALERT.rich(lang=marked_user.lang))
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
        RegistrationMessages.TIMEZONE_PROMPT.rich(first_name=DEFAULT_TG_USER_PARAMS["first_name"]),
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
        update, create_meeting_view(RenderContext(lang=user_with_settings.lang), datetime_link=datetime_link_content())
    )
    create_meeting = conversation_for(MeetingHandlerId.CREATE_MEETING_CONVERSATION)
    assert create_meeting._conversations.get(CONVERSATION_KEY) == ConversationMeetingState.TITLE
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_second_start_from_a_brand_new_user_stays_on_the_timezone_prompt(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    await process_update(routing_app, update)
    created_user = store_created_user(mock_session)

    api = await process_update(routing_app, update)

    assert_still_onboarding(api, update, created_user)


@pytest.mark.parametrize(
    ("update", "second_payload", "acquisition_source"),
    [
        (UpdateRequest(command="start", command_args="src_web"), "src_blog", "src_web"),
        (UpdateRequest(command="start", command_args="inline"), "inline", "inline"),
        (UpdateRequest(command="start", command_args="src_web"), "inline", "src_web"),
    ],
    indirect=["update"],
    ids=["acquisition_twice", "inline_twice", "acquisition_then_inline"],
)
async def test_second_deep_link_tap_stays_on_the_timezone_prompt_and_keeps_the_first_source(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
    second_payload: str,
    acquisition_source: str,
):
    await process_update(routing_app, update)
    created_user = store_created_user(mock_session)
    second_tap = create_update(UpdateRequest(command="start", command_args=second_payload))

    api = await process_update(routing_app, second_tap)

    assert_still_onboarding(api, second_tap, created_user)
    create_meeting = conversation_for(MeetingHandlerId.CREATE_MEETING_CONVERSATION)
    assert create_meeting._conversations.get(CONVERSATION_KEY) is None
    assert created_user.acquisition_source == acquisition_source


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_start_after_a_restart_lost_the_conversation_prompts_an_unfinished_user_again(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    await process_update(routing_app, update)
    created_user = store_created_user(mock_session)
    conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)._conversations.clear()

    api = await process_update(routing_app, update)

    assert_still_onboarding(api, update, created_user)


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_pairing_link_during_onboarding_asks_for_setup_and_keeps_the_timezone_prompt_open(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    await process_update(routing_app, update)
    created_user = store_created_user(mock_session)
    pairing_tap = create_update(UpdateRequest(command="start", command_args=f"{PAIRING_DEEP_LINK_PREFIX}_code"))

    api = await process_update(routing_app, pairing_tap)

    api.assert_send_message_called(pairing_tap, patreon_link_needs_setup_view(created_user.lang))
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) == ConversationRegistrationProcessState.TIMEZONE


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_answer_after_the_row_was_purged_asks_for_start_and_start_sets_the_user_up_again(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
):
    """The created row is never stored, which is what a lookup sees once the sweep has deleted it."""
    await process_update(routing_app, update)
    answer = create_update(UpdateRequest(message_text="Madrid"))

    answer_api = await process_update(routing_app, answer)

    notice = CommonMessages.ACCOUNT_NOT_FOUND.rich(lang=TranslationEngine.FALLBACK_LANG)
    answer_api.assert_send_message_called(answer, notice)

    start_api = await process_update(routing_app, update)

    start_api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_PROMPT.rich(first_name=DEFAULT_TG_USER_PARAMS["first_name"]),
    )
    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) == ConversationRegistrationProcessState.TIMEZONE


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_main_menu_appears_only_after_the_timezone_is_answered(
    routing_app: Application,
    update: Update,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
):
    await process_update(routing_app, update)
    created_user = store_created_user(mock_session)
    second_start_api = await process_update(routing_app, update)
    assert_still_onboarding(second_start_api, update, created_user)
    get_timezone_from_api.return_value = "Europe/Madrid"
    answer = create_update(UpdateRequest(message_text="Madrid"))

    answer_api = await process_update(routing_app, answer)

    registration = conversation_for(RegistrationProcessHandlerId.TIMEZONE_CONVERSATION)
    assert registration._conversations.get(CONVERSATION_KEY) is None
    assert created_user.status is UserStatus.MEMBER
    assert created_user.member_time is not None
    lang = created_user.lang
    user_guide = RichContent.link(
        RegistrationMessages.USER_GUIDE_LABEL.text(lang=lang), "https://mitup.social/user-guide/"
    )
    welcome = RegistrationMessages.REGISTRATION_COMPLETE.rich(
        timezone="Europe/Madrid", user_guide=user_guide, lang=lang
    )
    answer_api.assert_send_message_called(
        answer, main_menu_view(RenderContext(lang=lang), message=welcome, counts=MeetingCounts(0, 0, 0))
    )

    register_member_lookup(mock_session, created_user)
    final_start_api = await process_update(routing_app, update)

    final_start_api.assert_send_message_called(
        update, main_menu_view(RenderContext(lang=lang), counts=MeetingCounts(0, 0, 0))
    )
    assert registration._conversations.get(CONVERSATION_KEY) is None
