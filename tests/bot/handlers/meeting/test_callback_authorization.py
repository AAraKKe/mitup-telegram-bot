"""Authorization of the meeting id carried in callback data.

Callback data is client-supplied — a modified Telegram client can send any `data` bytes for any
message the bot posted, and `meetups.id` is sequential — so a handler that acts on the id alone
lets anyone reach any meeting. These tests pin, per handler, that a forged id from a user with no
claim on the meeting changes nothing and renders nothing, while every way a user legitimately
reaches a meeting keeps working.
"""

import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.enums import ConversationInviteState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils.messages import MeetingDisplayMessages, MeetingInviteMessages, MeetingJoinMessages
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_message,
    create_user,
)
from tests.helpers.context import StubMitupContext
from tests.helpers.conversation import ConversationStep, ConversationTester
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.types import ClaimSharedCard

# A meeting id the acting user has no relationship with, distinct from the ids the shared fixtures use.
FORGED_MEETING_ID = 777


@pytest.fixture
def stranger_meeting() -> Meetup:
    """A private meeting owned by somebody other than the acting user."""
    owner = create_user(id=500, tg_user_id=500, first_name="Owner")
    return create_meetup(
        id=FORGED_MEETING_ID,
        title="Private Meeting",
        description="Not for you",
        owner=owner,
        invitation=True,
    )


def register(mock_session: MockDbSession, user: User, meeting: Meetup):
    mock_session.add_object(user, query_field="tg_user_id")
    mock_session.add_object(meeting, "id")


def assert_rejected(context: StubMitupContext, meeting: Meetup, user: User, metrics: MetricAssertions):
    """The meeting was left untouched and nothing about it reached the caller."""
    assert meeting.joined_links == [], "a rejected caller must not gain a membership"
    assert meeting.messages == [], "a rejected caller's message must not become a tracked message"
    context.api.assert_update_meeting_messages_not_called()
    context.api.assert_answer_callback_query_called(
        update=context.get_update(),
        text=MeetingDisplayMessages.DELETED_BANNER.get_text(lang=user.lang),
        show_alert=True,
    )
    metrics.assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)


# --- Join / leave -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_join_with_forged_id_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Tapping any button in your own bot chat with the id swapped must not join you to a meeting.

    This is the cheapest form of the attack: the caller controls the chat the card would be
    rendered into, and the tracked message would keep pushing every later update there.
    """
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)
    mock_session.assert_not_flushed()
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.LEAVE.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_leave_with_forged_id_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Leave leaks the same card as join even when the membership change is a no-op."""
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_join_on_shared_card_is_allowed(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    claim_shared_card: ClaimSharedCard,
):
    """The first tap on a private meeting the owner shared must work for anyone holding the card.

    The card was tracked when it was sent, so this tap — from a user with no other relationship to
    the meeting — is authorized by that binding alone.
    """
    register(mock_session, user_with_settings, stranger_meeting)
    shared_card = claim_shared_card(stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(stranger_meeting.joined_links) == 1
    assert stranger_meeting.joined_links[0].user.db_id == user_with_settings.db_id
    # The tap reuses the tracked card and reveals the chat it lives in.
    assert stranger_meeting.messages == [shared_card]
    assert shared_card.chat_instance == "someinstance"
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_SUCCESS.get_text(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_join_on_shared_card_is_allowed_from_the_stored_claim_alone(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """The claim is read from the database, not from the meeting's loaded messages.

    Which tracked messages a meeting arrives with depends on how it was loaded, so the
    authorization must not hinge on that collection being complete.
    """
    register(mock_session, user_with_settings, stranger_meeting)
    mock_session.add_object(
        create_message(inline_message_id="some_inline_message_id", meetup_id=FORGED_MEETING_ID), "inline_message_id"
    )

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(stranger_meeting.joined_links) == 1
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.JOIN_SUCCESS.get_text(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_join_on_unclaimed_shared_card_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """An inline message no meeting claims must authorize nothing.

    Manufacturing one costs an attacker a single step — share a meeting of their own, into their
    own Saved Messages if they like — and they can then tap its buttons with the meeting id in the
    callback data swapped for somebody else's.
    """
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)
    mock_session.assert_not_flushed()


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_join_on_card_claimed_by_another_meeting_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A shared card that already belongs to another meeting cannot be borrowed for a forged id."""
    register(mock_session, user_with_settings, stranger_meeting)
    # The tapped inline message is tracked for a different meeting.
    other_meeting_message = create_message(inline_message_id="some_inline_message_id", meetup_id=1)
    mock_session.add_object(other_meeting_message, "inline_message_id")

    context, _ = await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_join_public_meeting_is_allowed(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A public meeting is open to anyone, so no message binding is required."""
    stranger_meeting.public = True
    register(mock_session, user_with_settings, stranger_meeting)

    await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(stranger_meeting.joined_links) == 1


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.LEAVE.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_participant_can_leave_from_bot_chat(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A participant reaches the card through the joined-meetings screen, which tracks no message."""
    create_joined_link(user=user_with_settings, meetup=stranger_meeting)
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.LEAVE, handler_context=handler_context)

    assert stranger_meeting.joined_links == []
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingJoinMessages.LEAVE_SUCCESS.get_text(lang=user_with_settings.lang),
        show_alert=False,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(1), from_bot_chat=True)],
    indirect=True,
)
async def test_owner_can_join_own_meeting_from_bot_chat(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """The owner acts on the main view, which is not a tracked message either."""
    meeting = user_with_settings.meetups[0]
    register(mock_session, user_with_settings, meeting)

    await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(meeting.joined_links) == 1


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.JOIN.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_join_allowed_when_meeting_is_tracked_in_this_chat(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """A meeting that already has a tracked message in this chat genuinely lives here."""
    stranger_meeting.messages.append(
        create_message(inline_message_id="earlier_share", chat_instance="someinstance", meetup_id=FORGED_MEETING_ID)
    )
    register(mock_session, user_with_settings, stranger_meeting)

    await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    assert len(stranger_meeting.joined_links) == 1


# --- Attach to chat -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_attach_to_chat_with_forged_id_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Attaching publishes a private meeting into a chat's inline search, so it must be authorized.

    The stored `chat_instance` is what `search_chat_meetings` resolves meetings by, so a forged
    attach would make somebody else's private meeting searchable in a chat the caller controls.
    """
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_on_shared_card_is_allowed(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
    claim_shared_card: ClaimSharedCard,
):
    """Attaching from the shared card itself is the flow the button exists for."""
    register(mock_session, user_with_settings, stranger_meeting)
    shared_card = claim_shared_card(stranger_meeting)

    await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert stranger_meeting.messages == [shared_card]
    assert shared_card.chat_instance == "someinstance"
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.ATTACH_TO_CHAT.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_attach_to_chat_on_unclaimed_shared_card_is_rejected(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A card of the caller's own making must not publish a stranger's meeting into their chat."""
    register(mock_session, user_with_settings, stranger_meeting)

    context, _ = await call_handler(MeetingHandlerId.ATTACH_TO_CHAT, handler_context=handler_context)

    assert_rejected(context, stranger_meeting, user_with_settings, metrics)
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.ATTACH_TO_CHAT)})


# --- Invite users -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.INVITE.with_id(FORGED_MEETING_ID), from_bot_chat=True)],
    indirect=True,
)
async def test_invite_with_forged_id_does_not_start_the_conversation(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A forged invite must not open the flow that seeds participants into the meeting."""
    register(mock_session, user_with_settings, stranger_meeting)

    context, state = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    assert state != ConversationInviteState.NAME
    assert stranger_meeting.joined_links == []
    context.api.assert_send_message_not_called()
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingDisplayMessages.DELETED_BANNER.get_text(lang=user_with_settings.lang),
        show_alert=True,
    )
    metrics.assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.INVITE.with_id(FORGED_MEETING_ID), from_bot_chat=False)],
    indirect=True,
)
async def test_invite_from_shared_card_is_allowed(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    claim_shared_card: ClaimSharedCard,
):
    """Inviting off a shared card is how a non-participant legitimately enters the flow."""
    register(mock_session, user_with_settings, stranger_meeting)
    claim_shared_card(stranger_meeting)

    context, state = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    assert state == ConversationInviteState.NAME
    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingInviteMessages.GO_PRIVATE.get_text(lang=user_with_settings.lang),
        show_alert=True,
    )


async def test_invite_confirm_cannot_be_redirected_to_another_meeting(
    user_with_settings: User,
    stranger_meeting: Meetup,
    mock_session: MockDbSession,
    conversation: ConversationTester,
):
    """The confirm button's meeting id is client-supplied, so it must match the authorized one.

    Without this, a caller could run the flow against a meeting they may invite into and then
    confirm against an arbitrary one, seeding a participant there and rendering its card.
    """
    own_meeting = user_with_settings.meetups[0]
    own_meeting.allow_invitation = True
    mock_session.add_user(user_with_settings)
    mock_session.add_object(own_meeting, "id")
    mock_session.add_object(stranger_meeting, "id")

    steps = [
        ConversationStep.callback(cb.INVITE.with_id(own_meeting.db_id), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
        # Same conversation, but the confirm names a meeting that was never authorized.
        ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(FORGED_MEETING_ID)),
    ]

    result = await conversation.run(handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION, steps=steps)

    confirm_context = result.last_context
    assert stranger_meeting.joined_links == [], "the forged meeting must not gain a participant"
    assert own_meeting.joined_links == [], "the authorized meeting must not be invited into either"
    confirm_context.api.assert_answer_callback_query_called(
        update=confirm_context.get_update(),
        text=MeetingInviteMessages.MEETING_NOT_FOUND.get_text(lang=user_with_settings.lang),
        show_alert=True,
    )
    # Each conversation step runs with its own metrics client, so assert on the confirm step's.
    MetricAssertions(confirm_context.metrics).assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)
    assert confirm_context.user_data is not None
    assert ContextId.INVITE_USERS not in confirm_context.user_data.registry
