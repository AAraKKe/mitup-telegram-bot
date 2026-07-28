import pytest
from telegram import Update

from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import InlineQueryMessages
from mitup_bot.views import MitupInlineView
from mitup_bot.views import meeting as meeting_views
from tests.helpers.context import HandlerContext, call_handler
from tests.helpers.fixtures import UpdateRequest, create_meetup, create_user
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


def meeting_unavailable_view(lang: str) -> MitupInlineView:
    """Locally rebuilt expected placeholder — kept independent of the production helper.

    Mirrors ``no_meetings_found_view`` in the sibling search test so that a broken
    production ``meeting_unavailable_view`` cannot make this assertion tautological.
    """
    return MitupInlineView(
        description=InlineQueryMessages.MEETING_UNAVAILABLE_MESSAGE.get(lang=lang),
        keyboard=[],
        id="meeting_unavailable",
        title=InlineQueryMessages.MEETING_UNAVAILABLE_TITLE.get(lang=lang),
        inline_description=InlineQueryMessages.MEETING_UNAVAILABLE_DESCRIPTION.get(lang=lang),
    )


@pytest.mark.parametrize(
    "update, meeting_id, is_owner, is_public, active, meeting_exists, should_share",
    [
        (UpdateRequest(inline_query="123"), 123, True, False, True, True, True),
        (UpdateRequest(inline_query="456"), 456, False, True, True, True, True),
        (UpdateRequest(inline_query="789"), 789, False, False, True, True, False),
        (UpdateRequest(inline_query="321"), 321, True, False, False, True, False),
        (UpdateRequest(inline_query="654"), 654, False, True, False, True, False),
        (UpdateRequest(inline_query="999"), 999, True, False, True, False, False),
    ],
    indirect=["update"],
    ids=[
        "owned_meeting",
        "public_non_owned",
        "private_not_owned",
        "inactive_owned",
        "inactive_public",
        "meeting_not_found",
    ],
)
async def test_share_meeting(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    meeting_id: int,
    is_owner: bool,
    is_public: bool,
    active: bool,
    meeting_exists: bool,
    should_share: bool,
):
    user = user_with_settings
    mock_session.add_user(user)

    meetup: Meetup | None = None
    if meeting_exists:
        owner = user if is_owner else create_user(id=999, tg_user_id=999, first_name="Owner")
        if not is_owner:
            mock_session.add_user(owner)
        meetup = create_meetup(meeting_id, "Meeting Title", owner=owner, public=is_public, active=active)
        mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    # Telegram inline queries must always be answered — even a cancelled/deleted/inaccessible
    # meeting gets an explicit placeholder result instead of a silently stalled picker (issue #176).
    if should_share:
        assert meetup is not None, "should_share implies the meeting was created"
        context.api.assert_answer_inline_query_called(
            update=update, results=[meeting_views.inline_view(meetup)], cache_time=0
        )
        metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    else:
        # The placeholder is the error handler's answer to the guard rejection, rendered in the
        # sharer's language because the rejection carries it.
        context.api.assert_answer_inline_query_called(
            update=update, results=[meeting_unavailable_view(user.lang)], cache_time=0
        )
        metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})

    # A rejection closes the interaction like a handler that ran to the end: whichever way the query
    # is answered, the fault series carries a single zero and no fault datapoint.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="456")], indirect=True)
async def test_share_meeting_public_meeting_by_unregistered_user(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A public meeting card carries a Share button in group chats, so the sharer may be any Telegram
    user — including one who never started the bot and therefore has no row in the database."""
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


@pytest.mark.parametrize("update", [UpdateRequest(inline_query="457")], indirect=True)
async def test_share_meeting_non_public_meeting_by_unregistered_user(
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A user without a profile owns nothing, so a non-public meeting stays behind the placeholder."""
    owner = create_user(id=999, tg_user_id=999, first_name="Owner")
    mock_session.add_user(owner)
    meetup = create_meetup(457, "Private Meeting", owner=owner, public=False)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    _, kwargs = context.api.call_args("answer_inline_query")
    answered = kwargs["results"][0]
    assert answered.id == "meeting_unavailable"
    assert "Private Meeting" not in answered.title
    # The rejection carries no account to read a language from, so the error handler renders the
    # placeholder in the fallback language.
    context.api.assert_answer_inline_query_called(
        update=update, results=[meeting_unavailable_view(TranslationEngine.FALLBACK_LANG)], cache_time=0
    )
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    # A stranger reaching a private meeting is a rejection, not a fault: it lands on the ownership
    # series and closes the interaction at zero faults.
    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
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
):
    """A user marked for deletion must not share anything tied to the dying account, ownership included."""
    marked_user = create_user(id=1, tg_user_id=123, first_name="Test", status=UserStatus.DELETION_REQUESTED)
    mock_session.add_user(marked_user)
    meetup = create_meetup(459, "Own Meeting", owner=marked_user, public=False)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)
    await context.flush_metrics()

    context.api.assert_answer_inline_query_called(
        update=update, results=[meeting_unavailable_view(TranslationEngine.FALLBACK_LANG)], cache_time=0
    )
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
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
async def test_share_meeting_malformed_query_answers_empty(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    # PTB matches the SHARE_MEETING pattern with re.match, not fullmatch, so a query with
    # leading digits (e.g. "123abc") reaches the handler. int(query) would raise and leave the
    # inline query silently unanswered (issue #178); the handler must answer with empty results.
    mock_session.add_user(user_with_settings)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, handler_context=handler_context)

    context.api.assert_answer_inline_query_called(update, results=[], cache_time=0)


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
