import pytest
from telegram import Update

from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey, MetricsClient
from mitup_bot.utils.messages import InlineQueryMessages
from mitup_bot.views import MitupInlineView
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
        context.api.assert_answer_inline_query_called(update=update, results=[meetup.inline_view()], cache_time=0)
        metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.SHARE_MEETING)})
    else:
        context.api.assert_answer_inline_query_called(
            update=update, results=[meeting_unavailable_view(user.lang)], cache_time=0
        )
        metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.SHARE_MEETING)})


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

    context.api.assert_answer_inline_query_called(update, results=[meetup.inline_view()], cache_time=0)
