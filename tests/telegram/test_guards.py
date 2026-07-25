import logging
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
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingInviteMessages
from mitup_bot.views import RenderContext, factory
from mitup_bot.views.mitup_view import MitupView
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


def deleted_meeting_view(lang: str, keyboard: Keyboard | None) -> MitupView:
    return MitupView(
        description=CommonMessages.DELETED_MEETING_ALERT.get(lang=lang),
        keyboard=keyboard
        or [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]],
    )


async def test_meeting_returns_meeting_owned_by_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(mock_session, user_with_settings, 1, "Test method", update, context)

        assert user_with_settings.meetups[0] == result
        assert caplog.text == ""

    context.api.assert_edit_message_not_called()
    context.api.assert_send_message_not_called()


async def test_meeting_touches_no_user_collections_under_lock(
    mock_session: MockDbSession,
    update: Update,
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

    result = await guards.meeting(mock_session, user_with_settings, 1, "Test method", update, context, lock=True)

    assert result == user_with_settings.meetups[0]
    mock_session.refresh.assert_not_awaited()


@pytest.mark.parametrize("keyboard", BACK_KEYBOARDS, ids=["without_custom_keyboard", "with_custom_keyboard"])
@pytest.mark.parametrize(
    "access", [MeetingAccess.OWNER, MeetingAccess.OWNER_OR_JOINED], ids=["owner", "owner_or_joined"]
)
async def test_meeting_shows_deleted_message_when_meeting_not_found(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    access: MeetingAccess,
    keyboard: Callable[[str], Keyboard | None],
    caplog: pytest.LogCaptureFixture,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Test method",
            update,
            context,
            access=access,
            custom_keyboard=keyboard(user_with_settings.lang),
        )
        # Call flush metrics that usually would be called by the handler
        await context.flush_metrics()

        assert "User tried 'Test method' with a meeting that does not exist." in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text
        assert result is None

        context.api.assert_edit_message_called(
            update, deleted_meeting_view(user_with_settings.lang, keyboard(user_with_settings.lang))
        )


async def test_meeting_redirects_when_meeting_not_owned(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
):
    meeting = create_meetup(999, "Meeting!", description="Description", owner=other_owner())
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(mock_session, user_with_settings, 999, "Test method", update, context)
        # Call flush metrics that usually would be called by the handler
        await context.flush_metrics()

        assert "User tried 'Test method' with a meeting that does not belong to them. " in caplog.text
        assert "Meeting id: 999, user id: 1" in caplog.text
        assert result is None

        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )
        metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)


async def test_meeting_returns_joined_meeting_not_owned_by_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    joined_meeting = create_meetup(id=7, owner=other_owner(), title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(joined_meeting)

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(
            mock_session,
            user_with_settings,
            7,
            "Show meeting",
            update,
            context,
            access=MeetingAccess.OWNER_OR_JOINED,
        )

        assert result == joined_meeting
        assert caplog.text == ""

    context.api.assert_edit_message_not_called()


async def test_meeting_redirects_when_meeting_neither_owned_nor_joined(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
):
    meeting = create_meetup(999, "Meeting!", description="Description", owner=other_owner())
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(meeting)

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Show meeting",
            update,
            context,
            access=MeetingAccess.OWNER_OR_JOINED,
        )
        await context.flush_metrics()

        assert result is None
        assert "User tried 'Show meeting' with a meeting that does not belong to them. " in caplog.text
        context.api.assert_edit_message_called(
            update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang))
        )
        metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)


@pytest.mark.parametrize("keyboard", BACK_KEYBOARDS, ids=["without_custom_keyboard", "with_custom_keyboard"])
@pytest.mark.parametrize(
    "access", [MeetingAccess.OWNER, MeetingAccess.OWNER_OR_JOINED], ids=["owner", "owner_or_joined"]
)
async def test_meeting_shows_reactivation_prompt_for_inactive_meeting_owned_by_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    access: MeetingAccess,
    keyboard: Callable[[str], Keyboard | None],
):
    inactive_meeting = create_meetup(id=5, owner=user_with_settings, active=False)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    result = await guards.meeting(
        mock_session,
        user_with_settings,
        5,
        "Show meeting",
        update,
        context,
        access=access,
        custom_keyboard=keyboard(user_with_settings.lang),
    )

    assert result is None
    context.api.assert_edit_message_called(
        update,
        factory.reactivation_prompt_view(
            RenderContext(lang=user_with_settings.lang), meeting_id=5, back_rows=keyboard(user_with_settings.lang)
        ),
    )


async def test_meeting_redirects_non_owner_of_inactive_meeting(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    """A participant of an inactive meeting is bounced to the main menu: only the owner can reactivate."""
    inactive_meeting = create_meetup(id=6, owner=other_owner(), active=False)
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=inactive_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    result = await guards.meeting(
        mock_session,
        user_with_settings,
        6,
        "Show meeting",
        update,
        context,
        access=MeetingAccess.OWNER_OR_JOINED,
    )
    await context.flush_metrics()

    assert result is None
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)


async def test_meeting_any_state_returns_inactive_meeting_owned_by_user(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
):
    """The past-meeting surfaces get the inactive meeting itself instead of the reactivation prompt."""
    inactive_meeting = create_meetup(id=5, owner=user_with_settings, active=False)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    result = await guards.meeting(
        mock_session,
        user_with_settings,
        5,
        "Show past meeting",
        update,
        context,
        access=MeetingAccess.OWNER_ANY_STATE,
    )

    assert result == inactive_meeting
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("registered", [True, False], ids=["not_owned", "not_found"])
async def test_meeting_any_state_redirects_when_meeting_is_not_the_users(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    metrics: MetricAssertions,
    registered: bool,
    caplog: pytest.LogCaptureFixture,
):
    """A meeting owned by somebody else and one that no longer exists get the same answer."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    if registered:
        mock_session.add_object(create_meetup(id=999, owner=other_owner(), active=False))

    with caplog.at_level(logging.WARNING):
        result = await guards.meeting(
            mock_session,
            user_with_settings,
            999,
            "Show past meeting",
            update,
            context,
            access=MeetingAccess.OWNER_ANY_STATE,
        )
        await context.flush_metrics()

    assert result is None
    assert "User tried 'Show past meeting' with a meeting that does not belong to them. " in caplog.text
    assert "Meeting id: 999, user id: 1" in caplog.text
    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)


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
