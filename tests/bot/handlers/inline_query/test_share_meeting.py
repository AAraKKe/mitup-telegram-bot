import logging

import pytest
from telegram import Update

from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import InlineQueryMessages
from mitup_bot.views import InlineResultsButton
from mitup_bot.views import meeting as meeting_views
from tests.helpers.context import HandlerContext, StubMitupContext, call_handler
from tests.helpers.fixtures import UpdateRequest, create_meetup, create_user
from tests.helpers.logs import log_record
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

# Spelled out here rather than imported from the handler so a reworded event name, level or reason
# fails the assertion instead of travelling silently into it.
SHARE_REJECTED_LOG_EVENT = "Meeting share rejected"


def registered_sharer_button(lang: str) -> InlineResultsButton:
    """The expected button, rebuilt locally so a broken production helper cannot make it vacuous."""
    return InlineResultsButton(text=InlineQueryMessages.CREATE_MEETING_BUTTON.text(lang=lang), start_parameter="inline")


def unregistered_sharer_button(lang: str) -> InlineResultsButton:
    return InlineResultsButton(text=InlineQueryMessages.EXPLORE_BUTTON.text(lang=lang), start_parameter="inline")


def assert_share_rejected(
    context: StubMitupContext,
    update: Update,
    caplog: pytest.LogCaptureFixture,
    *,
    reason: str,
    meeting_id: int | None,
    button: InlineResultsButton,
):
    """Assert the rejection was answered with the empty panel and named on one info line."""
    context.api.assert_answer_inline_query_called(update=update, results=[], button=button, cache_time=0)

    record = log_record(caplog, SHARE_REJECTED_LOG_EVENT)
    assert record.levelname == "INFO"
    assert record.__dict__["reason"] == reason
    assert record.__dict__["meeting_id"] == meeting_id


@pytest.mark.parametrize(
    "update, meeting_id, is_owner, is_public",
    [
        (UpdateRequest(inline_query="123"), 123, True, False),
        (UpdateRequest(inline_query="456"), 456, False, True),
    ],
    indirect=["update"],
    ids=["owned_meeting", "public_non_owned"],
)
async def test_share_meeting_answers_with_the_card(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    meeting_id: int,
    is_owner: bool,
    is_public: bool,
):
    user = user_with_settings
    mock_session.add_user(user)
    owner = user if is_owner else create_user(id=999, tg_user_id=999, first_name="Owner")
    if not is_owner:
        mock_session.add_user(owner)
    meetup = create_meetup(meeting_id, "Meeting Title", owner=owner, public=is_public, active=True)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    context.api.assert_answer_inline_query_called(
        update=update, results=[meeting_views.inline_view(meetup)], cache_time=0
    )
    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize(
    "update, meeting_id, is_owner, is_public, active, meeting_exists, reason",
    [
        (UpdateRequest(inline_query="789"), 789, False, False, True, True, "meeting_not_shareable"),
        (UpdateRequest(inline_query="321"), 321, True, False, False, True, "meeting_finished"),
        (UpdateRequest(inline_query="654"), 654, False, True, False, True, "meeting_not_shareable"),
        (UpdateRequest(inline_query="999"), 999, True, False, True, False, "meeting_not_found"),
    ],
    indirect=["update"],
    ids=["private_not_owned", "inactive_owned", "inactive_public", "meeting_not_found"],
)
async def test_share_meeting_rejection_answers_an_empty_panel(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
    meeting_id: int,
    is_owner: bool,
    is_public: bool,
    active: bool,
    meeting_exists: bool,
    reason: str,
):
    """Every reason a share can be refused is answered identically, and told apart on the log line.

    An id that resolves to nothing and one that resolves to somebody else's private meeting must be
    indistinguishable on the wire, or the picker becomes an id-enumeration oracle.
    """
    user = user_with_settings
    mock_session.add_user(user)
    if meeting_exists:
        owner = user if is_owner else create_user(id=999, tg_user_id=999, first_name="Owner")
        if not is_owner:
            mock_session.add_user(owner)
        mock_session.add_object(
            create_meetup(meeting_id, "Meeting Title", owner=owner, public=is_public, active=active)
        )
    mock_session.commit()

    with caplog.at_level(logging.INFO):
        context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    assert_share_rejected(
        context,
        update,
        caplog,
        reason=reason,
        meeting_id=meeting_id,
        button=registered_sharer_button(user.lang),
    )
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    # A keystroke that lands on a meeting the sharer cannot share is a normal interaction, so it is
    # neither counted as an ownership violation nor closed as a fault.
    metrics.assert_not_emitted(name=MetricKey.MEETING_NOT_OWNED)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(inline_query="123abc"),
        UpdateRequest(inline_query="12 34"),
        UpdateRequest(inline_query="123①"),
    ],
    indirect=["update"],
    ids=["digits_with_trailing_junk", "digits_with_inner_space", "digits_with_non_decimal_digit"],
)
async def test_share_meeting_non_numeric_query_answers_an_empty_panel(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    # PTB matches the SHARE_MEETING pattern with re.match, not fullmatch, so a query with leading
    # digits (e.g. "123abc") reaches the handler and names no meeting at all.
    mock_session.add_user(user_with_settings)
    mock_session.commit()

    with caplog.at_level(logging.INFO):
        context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)

    assert_share_rejected(
        context,
        update,
        caplog,
        reason="non_numeric_inline_query",
        meeting_id=None,
        button=registered_sharer_button(user_with_settings.lang),
    )


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="457")], indirect=True)
async def test_share_meeting_rejection_offers_an_unregistered_sharer_the_tour(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
):
    """A sharer with no account owns nothing, so a private meeting is refused and they are invited in."""
    owner = create_user(id=999, tg_user_id=999, first_name="Owner")
    mock_session.add_user(owner)
    mock_session.add_object(create_meetup(457, "Private Meeting", owner=owner, public=False))
    mock_session.commit()

    with caplog.at_level(logging.INFO):
        context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    assert_share_rejected(
        context,
        update,
        caplog,
        reason="meeting_not_shareable",
        meeting_id=457,
        button=unregistered_sharer_button(TranslationEngine.FALLBACK_LANG),
    )
    assert log_record(caplog, SHARE_REJECTED_LOG_EVENT).__dict__["user_id"] is None
    metrics.assert_not_emitted(name=MetricKey.MEETING_NOT_OWNED)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    mock_session.assert_not_added()


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="456")], indirect=True)
async def test_share_meeting_public_meeting_by_unregistered_user(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A public meeting card carries a Share button in group chats, so the sharer may be any Telegram
    user, including one who never started the bot and therefore has no row in the database."""
    owner = create_user(id=999, tg_user_id=999, first_name="Owner")
    mock_session.add_user(owner)
    meetup = create_meetup(456, "Public Meeting", owner=owner, public=True)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    _, kwargs = context.api.call_args("answer_inline_query")
    shared = kwargs["results"][0]
    assert shared.id == "456"
    assert shared.title == "Public Meeting"
    context.api.assert_answer_inline_query_called(
        update=update, results=[meeting_views.inline_view(meetup)], cache_time=0
    )
    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    # Sharing without an account is a normal interaction: no user row is created and nothing faults.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    mock_session.assert_not_added()


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="458")], indirect=True)
async def test_share_meeting_public_meeting_by_pending_deletion_user(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A user marked for deletion counts as unregistered: a public meeting still resolves on its own flag."""
    marked_user = create_user(id=1, tg_user_id=123, first_name="Test", status=UserStatus.DELETION_REQUESTED)
    mock_session.add_user(marked_user)
    owner = create_user(id=999, tg_user_id=999, first_name="Owner")
    meetup = create_meetup(458, "Public Meeting", owner=owner, public=True)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    _, kwargs = context.api.call_args("answer_inline_query")
    shared = kwargs["results"][0]
    assert shared.id == "458"
    assert shared.title == "Public Meeting"
    context.api.assert_answer_inline_query_called(
        update=update, results=[meeting_views.inline_view(meetup)], cache_time=0
    )
    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    # The dying account is never touched: the meeting resolves on its own flag, with no fault and no
    # pending-deletion alert to answer the query with.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="459")], indirect=True)
async def test_share_meeting_own_meeting_by_pending_deletion_user(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    caplog: pytest.LogCaptureFixture,
):
    """A user marked for deletion must not share anything tied to the dying account, ownership included."""
    marked_user = create_user(id=1, tg_user_id=123, first_name="Test", status=UserStatus.DELETION_REQUESTED)
    mock_session.add_user(marked_user)
    mock_session.add_object(create_meetup(459, "Own Meeting", owner=marked_user, public=False))
    mock_session.commit()

    with caplog.at_level(logging.INFO):
        context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    assert_share_rejected(
        context,
        update,
        caplog,
        reason="meeting_not_shareable",
        meeting_id=459,
        button=unregistered_sharer_button(TranslationEngine.FALLBACK_LANG),
    )
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(inline_query="555 ")],
    indirect=["update"],
    ids=["trailing_space_valid_id"],
)
async def test_share_meeting_strips_surrounding_whitespace(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # The SHARE_MEETING pattern (\d+, re.match) still matches a trailing-space query like "555 ",
    # so it reaches the handler; .strip() must let it resolve to meeting 555 instead of answering empty.
    user = user_with_settings
    mock_session.add_user(user)
    meetup = create_meetup(555, "Meeting Title", owner=user, active=True)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)

    context.api.assert_answer_inline_query_called(update, results=[meeting_views.inline_view(meetup)], cache_time=0)
