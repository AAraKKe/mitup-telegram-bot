import datetime as dt
from collections.abc import Callable

import pytest
from telegram import Update
from telegram.ext import Application

import mitup_bot.utils.callbacks as cb
from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import User
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import ButtonMessages, InlineViewMessages
from mitup_bot.views import ButtonConfig, InlineResultsButton, MitupInlineView
from tests.helpers import create_meetup, create_user
from tests.helpers.context import call_handler
from tests.helpers.fixtures import UpdateRequest
from tests.helpers.stub_db import MockDbSession


def _chat_card(lang: str) -> MitupInlineView:
    return MitupInlineView(
        description=InlineViewMessages.MEETINGS_IN_THIS_CHAT_MESSAGE.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.LOAD_CHAT_MEETINGS.get(lang=lang),
                    callback_data=cb.LOAD_CHAT_MEETINGS,
                )
            ],
        ],
        id="meetings_in_this_chat",
        title=InlineViewMessages.MEETINGS_IN_THIS_CHAT_TITLE.get(lang=lang),
        inline_description=InlineViewMessages.MEETINGS_IN_THIS_CHAT_DESCRIPTION.get(lang=lang),
    )


def _inline_button(lang: str) -> InlineResultsButton:
    return InlineResultsButton(
        text=InlineViewMessages.CREATE_NEW_MEETING_BUTTON.get_text(lang=lang),
        start_parameter="inline",
    )


def _explore_button(lang: str) -> InlineResultsButton:
    return InlineResultsButton(
        text=InlineViewMessages.EXPLORE_MITUP_BUTTON.get_text(lang=lang),
        start_parameter="inline",
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_returns_results_and_button(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    mock_session.add_user(user_with_settings)

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_method_just_called("answer_inline_query")
    _, kwargs = context.api.call_args("answer_inline_query")

    # Verify the button is an InlineResultsButton with a start_parameter
    button = kwargs.get("button")
    assert isinstance(button, InlineResultsButton)
    assert button.start_parameter == "inline"

    # Verify the first result is the "meetings in this chat" article with a load button
    results = kwargs.get("results")
    assert results is not None
    assert results[0].id == "meetings_in_this_chat"

    # The inline result should have a keyboard with the "Load meetings" button
    keyboard = results[0].keyboard
    assert len(keyboard) == 1
    load_button = keyboard[0][0]
    assert load_button.callback_data == cb.LOAD_CHAT_MEETINGS
    assert load_button.text == ButtonMessages.LOAD_CHAT_MEETINGS.get(lang=user_with_settings.lang)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_uses_user_language(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
):
    """When the user has a mitup profile, inline view messages should use their language."""
    mock_session.add_user(user_with_settings)
    lang = user_with_settings.lang

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    _, kwargs = context.api.call_args("answer_inline_query")
    button = kwargs["button"]
    assert button.text == InlineViewMessages.CREATE_NEW_MEETING_BUTTON.get_text(lang=lang)

    results = kwargs["results"]
    assert results[0].title == InlineViewMessages.MEETINGS_IN_THIS_CHAT_TITLE.get_text(lang=lang)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_falls_back_to_default_language_for_unknown_user(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
):
    """When the user does not have a mitup profile, inline view should fall back to the default language."""
    default_lang = TranslationEngine.FALLBACK_LANG

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[_chat_card(default_lang)],
        button=_explore_button(default_lang),
        cache_time=0,
    )


# --- User meetings in inline view ---

DEFAULT_LANG = "en"


def _no_meetings_scenario() -> tuple[User, list[MitupInlineView]]:
    """User has no meetings — only the chat card should appear."""
    return create_user(id=1, tg_user_id=123, first_name="Test"), []


def _active_meetings_scenario() -> tuple[User, list[MitupInlineView]]:
    """User has active meetings — they should appear after the chat card."""
    m1 = create_meetup(10, "Meeting A")
    m2 = create_meetup(11, "Meeting B")
    user = create_user(id=1, tg_user_id=123, first_name="Test", owned_meetings=[m1, m2])
    return user, [m1.inline_view(), m2.inline_view()]


def _mixed_active_inactive_scenario() -> tuple[User, list[MitupInlineView]]:
    """User has both active and inactive meetings — only active ones shown."""
    active = create_meetup(10, "Active")
    inactive = create_meetup(11, "Inactive", active=False)
    user = create_user(id=1, tg_user_id=123, first_name="Test", owned_meetings=[active, inactive])
    return user, [active.inline_view()]


@pytest.mark.parametrize(
    "update, build_scenario",
    [
        (UpdateRequest(inline_query=" "), _no_meetings_scenario),
        (UpdateRequest(inline_query=" "), _active_meetings_scenario),
        (UpdateRequest(inline_query=" "), _mixed_active_inactive_scenario),
    ],
    indirect=["update"],
    ids=["no_meetings", "active_meetings", "mixed_active_inactive"],
)
async def test_inline_view_includes_user_meetings(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
    build_scenario: Callable[[], tuple[User, list[MitupInlineView]]],
):
    user, expected_meeting_views = build_scenario()
    mock_session.add_user(user)

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[_chat_card(DEFAULT_LANG), *expected_meeting_views],
        button=_inline_button(DEFAULT_LANG),
        cache_time=0,
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_sorts_user_meetings_by_relevance(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
):
    """Future meetings first, then no datetime, then past."""
    now = dt.datetime.now(tz=dt.UTC)

    past = create_meetup(1, "Past", datetime=now - dt.timedelta(days=7))
    future = create_meetup(2, "Future", datetime=now + dt.timedelta(days=7))
    no_date = create_meetup(3, "No Date")

    user = create_user(id=1, tg_user_id=123, first_name="Test", owned_meetings=[past, future, no_date])
    mock_session.add_user(user)

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[
            _chat_card(DEFAULT_LANG),
            future.inline_view(),
            no_date.inline_view(),
            past.inline_view(),
        ],
        button=_inline_button(DEFAULT_LANG),
        cache_time=0,
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query=" ")], indirect=True)
async def test_inline_view_sorts_no_datetime_meetings_by_created_time(
    update: Update,
    mock_session: MockDbSession,
    app: Application,
):
    """Meetings without a datetime should be ordered by created_time ascending."""
    base = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)

    late = create_meetup(1, "Late", created_time=base + dt.timedelta(hours=2))
    early = create_meetup(2, "Early", created_time=base)
    middle = create_meetup(3, "Middle", created_time=base + dt.timedelta(hours=1))

    user = create_user(id=1, tg_user_id=123, first_name="Test", owned_meetings=[late, early, middle])
    mock_session.add_user(user)

    context, _ = await call_handler(InlineQueryId.INLINE_VIEW, update=update, app=app)

    context.api.assert_answer_inline_query_called(
        update=update,
        results=[
            _chat_card(DEFAULT_LANG),
            early.inline_view(),
            middle.inline_view(),
            late.inline_view(),
        ],
        button=_inline_button(DEFAULT_LANG),
        cache_time=0,
    )
