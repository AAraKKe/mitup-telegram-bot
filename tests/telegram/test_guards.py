import re
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Chat, Message, Update

from mitup_bot import guards
from mitup_bot.callback_data import CallbackData, MeetingListSource, PaginatedCallbackData
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    InlineQueryNotSetError,
    MalformedCallbackData,
    MeetingGoneError,
    MeetingInactiveOwnerError,
    MeetingNotOwnedError,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.guards import (
    MeetingAccess,
    callback_query,
    chat,
    current_user,
    is_admin,
    message,
    render_context,
    user_language,
    user_registered,
    valid_callback_data,
    valid_callback_query,
    valid_inline_query,
    valid_paginated_callback_data,
)
from mitup_bot.handlers.main_menu import MainMenuHandlerId
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingInviteMessages
from mitup_bot.views import RenderContext
from tests.helpers import (
    StubMitupContext,
    UpdateRequest,
    create_bot_config,
    create_joined_link,
    create_meetup,
    create_member,
    create_user,
)
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


def set_admin_ids(context: StubMitupContext, admin_ids: list[int]):
    context.bot_data[BOT_CONFIG_KEY] = create_bot_config(admin_ids)


def test_is_admin_true_for_allowlisted_user(context: StubMitupContext, update: Update):
    set_admin_ids(context, [DEFAULT_USER_ID])

    assert is_admin(update, context) is True


def test_is_admin_false_for_non_allowlisted_user(context: StubMitupContext, update: Update):
    set_admin_ids(context, [DEFAULT_USER_ID + 1])

    assert is_admin(update, context) is False


def test_is_admin_false_for_empty_allowlist(context: StubMitupContext, update: Update):
    set_admin_ids(context, [])

    assert is_admin(update, context) is False


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_is_admin_false_without_effective_user(context: StubMitupContext, update: Update):
    set_admin_ids(context, [DEFAULT_USER_ID])

    assert is_admin(update, context) is False


def test_render_context_carries_user_lang_and_admin_flag(
    context: StubMitupContext, update: Update, user_with_settings: User
):
    set_admin_ids(context, [DEFAULT_USER_ID])

    assert render_context(user_with_settings, update, context) == RenderContext(
        lang=user_with_settings.lang, is_admin=True
    )


def test_render_context_marks_non_admin_users(context: StubMitupContext, update: Update, user_with_settings: User):
    set_admin_ids(context, [])

    assert render_context(user_with_settings, update, context) == RenderContext(
        lang=user_with_settings.lang, is_admin=False
    )


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
async def test_current_user_fails_without_effective_user(mock_session: AsyncSession, update: Update):
    with pytest.raises(EffectiveUserNotSet):
        await current_user(update, mock_session)


async def test_current_user_fails_if_user_not_in_db(mock_session: MockDbSession, update: Update):
    with pytest.raises(UserNotFound):
        await current_user(update, mock_session)


async def test_current_user_succeeds(mock_session: MockDbSession, update: Update, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert user_with_settings == await current_user(update, mock_session)


async def test_current_user_rejects_user_pending_deletion(mock_session: MockDbSession, update: Update):
    marked_user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, status=UserStatus.DELETION_REQUESTED)
    mock_session.add_object(marked_user, "tg_user_id")

    with pytest.raises(UserPendingDeletion) as raised:
        await current_user(update, mock_session)

    assert raised.value.tg_user_id == marked_user.tg_user_id
    assert raised.value.lang == marked_user.lang


async def test_user_language_returns_user_lang(mock_session: MockDbSession, update: Update, user_with_settings: User):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert await user_language(update, mock_session) == user_with_settings.lang


async def test_user_language_falls_back_for_unknown_user(mock_session: MockDbSession, update: Update):
    assert await user_language(update, mock_session) == TranslationEngine.FALLBACK_LANG


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
async def test_user_language_falls_back_without_effective_user(mock_session: MockDbSession, update: Update):
    assert await user_language(update, mock_session) == TranslationEngine.FALLBACK_LANG


@pytest.mark.parametrize("update", [UpdateRequest(chat=False)], indirect=True)
def test_chat_fails_without_effective_chat(update: Update):
    with pytest.raises(EffectiveChatNotSet):
        chat(update)


def test_chat_succeeds(tg_chat: Chat, update: Update):
    assert tg_chat == chat(update)


@pytest.mark.parametrize("update", [UpdateRequest(message=False)], indirect=True)
def test_message_fails_without_effective_chat(update: Update):
    with pytest.raises(EffectiveMessageNotSet):
        message(update)


def test_message_succeeds(tg_message: Message, update: Update):
    assert tg_message == message(update)


@pytest.mark.parametrize(
    "update, expect",
    [
        (UpdateRequest(callback_query=False), pytest.raises(CallbackQueryNotSet)),
        (UpdateRequest(callback_query=True), nullcontext()),
    ],
    indirect=["update"],
    ids=["callback_query_not_set", "callback_query_set"],
)
def test_callback_query(update: Update, expect: AbstractContextManager):
    with expect:
        callback_query(update)


BACK_KEYBOARDS = [
    lambda lang: None,
    lambda lang: [
        [
            ButtonConfig(
                text=ButtonMessages.ACTIVE_MEETINGS.get_text(lang=lang),
                callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
            ),
        ]
    ],
]


def other_owner() -> User:
    """A second registered user, for the meetings the acting user does not own."""
    return create_member(id=999, tg_user_id=9990)


def assert_renders_nothing(context: StubMitupContext):
    """The guard resolves access and nothing else — every rejection screen is the error handler's."""
    context.api.assert_edit_message_not_called()
    context.api.assert_send_message_not_called()
    context.api.assert_method_just_called("answer_callback_query", times=0)
    context.api.assert_method_just_called("answer_inline_query", times=0)


async def test_meeting_returns_meeting_owned_by_user(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    result = await guards.meeting(mock_session, user_with_settings, 1, "Test method", context)
    await context.flush_metrics()

    assert user_with_settings.meetups[0] == result
    # The owner passing the guard is the zero datapoint of the rejection series.
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=0)
    assert_renders_nothing(context)


async def test_meeting_touches_no_user_collections_under_lock(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
):
    """Locking costs nothing on the acting user.

    Every decision the guard makes is rooted at the meeting, so it neither reads nor re-loads
    `user.meetups` / `user.joined_links` — a handler that wants them across the locked load
    re-loads them itself.
    """
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    result = await guards.meeting(mock_session, user_with_settings, 1, "Test method", context, lock=True)

    assert result == user_with_settings.meetups[0]
    mock_session.refresh.assert_not_awaited()


@pytest.mark.parametrize("keyboard", BACK_KEYBOARDS, ids=["without_custom_keyboard", "with_custom_keyboard"])
@pytest.mark.parametrize(
    "access", [MeetingAccess.OWNER, MeetingAccess.OWNER_OR_JOINED], ids=["owner", "owner_or_joined"]
)
async def test_meeting_raises_gone_when_meeting_not_found(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    access: MeetingAccess,
    keyboard: Callable[[str], Keyboard | None],
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    back_rows = keyboard(user_with_settings.lang)

    with pytest.raises(MeetingGoneError) as raised:
        await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Test method",
            context,
            access=access,
            custom_keyboard=back_rows,
        )

    error = raised.value
    assert str(error) == "User tried 'Test method' with a meeting that does not exist. Meeting id: 999, user id: 1"
    assert (error.meeting_id, error.action, error.lang) == (999, "Test method", user_with_settings.lang)
    # The back-navigation the caller asked for travels on the exception to the renderer.
    assert error.keyboard == back_rows
    assert_renders_nothing(context)


async def test_meeting_raises_not_owned_when_meeting_belongs_to_somebody_else(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    meeting = create_meetup(999, "Meeting!", description="Description", owner=other_owner())
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with pytest.raises(MeetingNotOwnedError) as raised:
        await guards.meeting(mock_session, user_with_settings, 999, "Test method", context)
    await context.flush_metrics()

    error = raised.value
    assert (
        str(error)
        == "User tried 'Test method' with a meeting that does not belong to them. Meeting id: 999, user id: 1"
    )
    assert (error.meeting_id, error.action, error.lang) == (999, "Test method", user_with_settings.lang)
    # Counting the rejection belongs to the error handler; a raising guard leaves the series alone.
    metrics.assert_not_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    assert_renders_nothing(context)


async def test_meeting_returns_joined_meeting_not_owned_by_user(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    joined_meeting = create_meetup(id=7, owner=other_owner(), title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(joined_meeting)

    result = await guards.meeting(
        mock_session,
        user_with_settings,
        7,
        "Show meeting",
        context,
        access=MeetingAccess.OWNER_OR_JOINED,
    )
    await context.flush_metrics()

    assert result == joined_meeting
    # The series tracks ownership decisions, so a participant getting through leaves it untouched.
    metrics.assert_not_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=0)
    assert_renders_nothing(context)


async def test_meeting_raises_not_owned_when_neither_owned_nor_joined(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
):
    meeting = create_meetup(999, "Meeting!", description="Description", owner=other_owner())
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with pytest.raises(MeetingNotOwnedError) as raised:
        await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Show meeting",
            context,
            access=MeetingAccess.OWNER_OR_JOINED,
        )

    assert "User tried 'Show meeting' with a meeting that does not belong to them. " in str(raised.value)
    assert_renders_nothing(context)


@pytest.mark.parametrize("keyboard", BACK_KEYBOARDS, ids=["without_custom_keyboard", "with_custom_keyboard"])
@pytest.mark.parametrize(
    "access", [MeetingAccess.OWNER, MeetingAccess.OWNER_OR_JOINED], ids=["owner", "owner_or_joined"]
)
async def test_meeting_raises_inactive_owner_for_inactive_meeting_owned_by_user(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    access: MeetingAccess,
    keyboard: Callable[[str], Keyboard | None],
):
    inactive_meeting = create_meetup(id=5, owner=user_with_settings, active=False)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)
    back_rows = keyboard(user_with_settings.lang)

    with pytest.raises(MeetingInactiveOwnerError) as raised:
        await guards.meeting(
            mock_session,
            user_with_settings,
            5,
            "Show meeting",
            context,
            access=access,
            custom_keyboard=back_rows,
        )

    error = raised.value
    assert (error.meeting_id, error.action, error.lang) == (5, "Show meeting", user_with_settings.lang)
    assert error.keyboard == back_rows
    assert_renders_nothing(context)


async def test_meeting_raises_not_owned_for_non_owner_of_inactive_meeting(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
):
    """A participant of an inactive meeting is stopped like any non-owner: only the owner can reactivate."""
    inactive_meeting = create_meetup(id=6, owner=other_owner(), active=False)
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=inactive_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    with pytest.raises(MeetingNotOwnedError):
        await guards.meeting(
            mock_session,
            user_with_settings,
            6,
            "Show meeting",
            context,
            access=MeetingAccess.OWNER_OR_JOINED,
        )

    assert_renders_nothing(context)


async def test_meeting_any_state_returns_inactive_meeting_owned_by_user(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    """The past-meeting surfaces get the inactive meeting itself instead of a rejection."""
    inactive_meeting = create_meetup(id=5, owner=user_with_settings, active=False)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    result = await guards.meeting(
        mock_session,
        user_with_settings,
        5,
        "Show past meeting",
        context,
        access=MeetingAccess.OWNER_ANY_STATE,
    )
    await context.flush_metrics()

    assert result == inactive_meeting
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=0)
    assert_renders_nothing(context)


@pytest.mark.parametrize("registered", [True, False], ids=["not_owned", "not_found"])
async def test_meeting_any_state_raises_not_owned_when_meeting_is_not_the_users(
    mock_session: MockDbSession,
    context: StubMitupContext,
    user_with_settings: User,
    registered: bool,
):
    """A meeting owned by somebody else and one that no longer exists get the same rejection."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    if registered:
        mock_session.add_object(create_meetup(id=999, owner=other_owner(), active=False))

    with pytest.raises(MeetingNotOwnedError) as raised:
        await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Show past meeting",
            context,
            access=MeetingAccess.OWNER_ANY_STATE,
        )

    error = raised.value
    assert "User tried 'Show past meeting' with a meeting that does not belong to them. " in str(error)
    assert "Meeting id: 999, user id: 1" in str(error)
    assert_renders_nothing(context)


@pytest.mark.parametrize(
    "match", [re.match(cb.SHOW_MEETING.pattern, "show;meeting:"), None], ids=["no_id", "none_match"]
)
def test_valid_callback_data_failed_states(match: re.Match | None):
    with pytest.raises(MalformedCallbackData):
        valid_callback_data(CallbackData.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)


@pytest.mark.parametrize(
    "match", [re.match(cb.SHOW_PAST_MEETING.pattern, "show;past_meeting:"), None], ids=["no_id", "none_match"]
)
def test_valid_paginated_callback_data_failed_states(match: re.Match | None):
    with pytest.raises(MalformedCallbackData):
        valid_paginated_callback_data(PaginatedCallbackData.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)


@pytest.mark.parametrize(
    "wire, expected_page, expected_source",
    [
        ("show;past_meeting:42;page:3", 3, None),
        ("show;past_meeting:42;page:3;src:a", 3, MeetingListSource.ACTIVE),
        ("show;past_meeting:42;page:3;src:j", 3, MeetingListSource.JOINED),
        # A missing page defaults to the first page; a missing source stays None.
        ("show;past_meeting:42", 1, None),
        # A source without an explicit page still defaults the page to 1 and passes the source through.
        ("show;past_meeting:42;src:a", 1, MeetingListSource.ACTIVE),
    ],
    ids=["with_page", "active_source", "joined_source", "page_defaults_to_one", "source_without_page"],
)
def test_valid_paginated_callback_data_page(wire: str, expected_page: int, expected_source: MeetingListSource | None):
    match = re.match(cb.SHOW_PAST_MEETING.pattern, wire)
    valid = valid_paginated_callback_data(cb.SHOW_PAST_MEETING.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)
    assert valid.id == 42
    assert valid.page == expected_page
    assert valid.source == expected_source


@pytest.mark.parametrize(
    "match",
    [
        re.match(cb.EDIT_MEETING_DATE.pattern, "edit;meet_date:;date:2024-12-02"),
        re.match(cb.EDIT_MEETING_DATE.pattern, "edit;meet_date:12;date:"),
        None,
    ],
    ids=["no_id", "no_date", "none_match"],
)
def test_valid_date_callback_data_failed_states(match: re.Match | None):
    with pytest.raises(MalformedCallbackData):
        valid_callback_data(CallbackData.parse(match), MainMenuHandlerId.MAIN_MENU_CALLBACK)


@pytest.mark.parametrize(
    "update, user_id, expectation",
    [
        [UpdateRequest(callback_query=CallbackData(entity="m", action="show", id=1)), 123, nullcontext()],
        [UpdateRequest(callback_query=CallbackData(entity="m", action="show", id=1)), 456, nullcontext()],
        [UpdateRequest(callback_query=False), 456, pytest.raises(CallbackQueryNotSet)],
    ],
    ids=["user_registered", "user_not_registered", "no_callback"],
    indirect=["update"],
)
async def test_context_manager_for_registered_user(
    mock_session: MockDbSession,
    update: Update,
    user_id: int,
    expectation: AbstractContextManager,
    context: StubMitupContext,
):
    # The update is created with a user with tg_user_id=123
    mock_session.add_user(create_user(1, tg_user_id=user_id))
    is_registered = user_id == 123

    with expectation:
        user = await user_registered(update, mock_session, context, MeetingInviteMessages.OPEN_CHAT)
        if is_registered:
            assert user is not None
            assert user.tg_user_id == user_id
        else:
            context.api.assert_answer_callback_query_called(
                update,
                MeetingInviteMessages.OPEN_CHAT.get(lang="en"),
                show_alert=True,
            )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="")], indirect=True)
def test_valid_inline_query_raises_when_no_inline_query(update: Update):
    # An update with inline_query="" produces Update(inline_query=None) per create_update logic
    with pytest.raises(InlineQueryNotSetError):
        valid_inline_query(update)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=False)], indirect=True)
def test_valid_callback_query_raises_when_no_callback_query(update: Update):
    with pytest.raises(CallbackQueryNotSet):
        valid_callback_query(update)
