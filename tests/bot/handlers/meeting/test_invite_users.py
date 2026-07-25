from collections.abc import Callable
from functools import partial

import pytest

from mitup_bot import views
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.enums import ConversationInviteState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.monitoring import Feature, MetricsClient, MetricUnit
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.utils import MeetingDisplayMessages, MeetingInviteMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, RenderContext
from mitup_bot.views import factory as views_factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    AnyFloat,
    MockDbSession,
    UpdateRequest,
    assert_locked_meetup_select,
    call_handler,
    create_joined_link,
    create_meetup,
    create_user,
    integrity_error,
)
from tests.helpers.conversation import ConversationStep, ConversationTester
from tests.helpers.handler_context import HandlerContext
from tests.helpers.monitoring import MetricAssertions

MEETING_ID = 999


@pytest.fixture
def meeting() -> Meetup:
    return create_meetup(
        id=MEETING_ID,
        title="Test Meeting",
        description="Meeting to invite people",
        invitation=True,
    )


def setup_db(mock_session: MockDbSession, user: User, meeting: Meetup):
    mock_session.add_user(user)
    mock_session.add_object(meeting, "id")
    user.meetups.append(meeting)


def setup_inviter(mock_session: MockDbSession, user: User, meeting: Meetup, owner_id: int) -> User:
    """Wire `user` up as the acting inviter, owning the meeting or merely participating in it.

    A non-owner only reaches the invite button in their bot chat by holding the meeting card there,
    which the joined-meetings screen renders for participants, so the non-owner case joins them to
    the meeting instead of leaving them unrelated to it.
    """
    if owner_id == 123:
        owner = user
    else:
        owner = create_user(id=owner_id, tg_user_id=owner_id, first_name="Other owner")
        mock_session.add_user(user)
        create_joined_link(user=user, meetup=meeting)
    setup_db(mock_session, owner, meeting)
    return owner


@pytest.mark.parametrize(
    "update, external_chat",
    [
        [UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID), from_bot_chat=True), False],
        [UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID), from_bot_chat=False), True],
    ],
    indirect=["update"],
    ids=["bot_chat", "external_chat"],
)
async def test_invite_users_by_registered_user(
    handler_context: HandlerContext,
    external_chat: bool,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
):
    setup_db(mock_session, user_with_settings, meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    expected_view = views.factory.request_information_with_cancel_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingInviteMessages.PROMPT.get(lang=user_with_settings.lang),
        callback_data=cb.CANCEL_INVITE_USER.with_id(MEETING_ID),
    )

    if external_chat:
        context.api.assert_answer_callback_query_called(
            handler_context.update,
            text=MeetingInviteMessages.GO_PRIVATE.get(lang=user_with_settings.lang),
            show_alert=True,
        )
    context.api.assert_send_message_to_user_called(user_with_settings, expected_view)


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID)),
    ],
    indirect=True,
)
async def test_invite_users_by_unregistered_user(
    handler_context: HandlerContext,
    mock_session: MockDbSession,
    user_with_settings: User,
    meeting: Meetup,
):
    user = create_user(id=100, tg_user_id=456)
    setup_db(mock_session, user, meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=MeetingInviteMessages.OPEN_CHAT.get(lang=user_with_settings.lang),
        show_alert=True,
    )


async def test_invite_with_id_of_meeting_does_not_exist(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    """The invite button on a card whose meeting is gone gets the same answer join and leave give it.

    The conversation runs in the bot's own chat, so the banner that replaces the card there carries the
    main-menu row: it is the whole screen the user is left looking at.
    """
    mock_session.add_user(user_with_settings)
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    result.last_context.api.assert_edit_message_called(
        update=result.last_context.get_update(),
        view=MitupView(
            description=MeetingDisplayMessages.DELETED_BANNER.get(lang=user_with_settings.lang),
            keyboard=views_factory.main_menu_back_rows(user_with_settings.lang),
        ),
    )
    MetricAssertions(result.last_context.metrics).assert_emitted(name=MetricKey.STALE_MEETING_MESSAGE, value=1.0)


async def test_invite_users_ask_for_name(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
):
    setup_db(mock_session, user_with_settings, meeting)

    # These are the steps for the conversation when asking for a name
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    callback_step = result.get_step(0)
    message_step = result.get_step(1)

    assert callback_step.state is ConversationInviteState.NAME
    assert message_step.state is ConversationInviteState.CONFIRMATION

    # User has been asked to confirm the name
    expected_view = views_factory.confirmation_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingInviteMessages.CONFIRMATION.get(
            lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title
        ),
        confirm_callback_data=cb.CONFIRM_INVITE_USER.with_id(MEETING_ID),
        decline_callback_data=cb.CANCEL_INVITE_USER.with_id(MEETING_ID),
    )

    message_step.context.api.assert_send_message_to_user_called(
        user_with_settings,
        expected_view,
    )

    # The name has been stored in the user data
    with message_step.context.text(ContextId.INVITE_USERS) as text:
        assert text == "Bruce Wayne"


@pytest.mark.parametrize(
    "owner_id",
    [123, 456],
    ids=["by_owner", "by_other_user"],
)
async def test_cancel_name_request(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    owner_id: int,
    meeting: Meetup,
):
    setup_inviter(mock_session, user_with_settings, meeting, owner_id)

    # These are the steps for the conversation when cancelling the name request
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.callback(cb.CANCEL_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    callback_step = result.get_step(0)
    cancel_step = result.get_step(1)

    assert callback_step.state is ConversationInviteState.NAME
    assert cancel_step.state is None  # Conversation has ended

    message = MeetingInviteMessages.CANCELED.get(lang=user_with_settings.lang)

    # Owners are returned to their meeting; everyone else lands on the main menu
    if owner_id == 123:
        expected_view = meeting_views.view_for(meeting, user_with_settings).with_context(message)
    else:
        expected_view = views.factory.main_menu_view(RenderContext(lang=user_with_settings.lang), message=message)

    cancel_step.context.api.assert_edit_message_called(
        update=cancel_step.context.get_update(),
        view=expected_view,
    )


@pytest.mark.parametrize(
    "owner_id",
    [123, 456],
    ids=["by_owner", "by_other_user"],
)
async def test_complete_user_invitation(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    owner_id: int,
    meeting: Meetup,
):
    setup_inviter(mock_session, user_with_settings, meeting, owner_id)

    # These are the steps for the full conversation of inviting a user
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
        ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    confirm_context = result.last_context

    # User has been sent confirmation of the invitation
    # With the proper view depending on who invited the user
    expected_view = meeting_views.main_view(meeting) if owner_id == 123 else meeting_views.external_view(meeting)
    expected_view = expected_view.with_context(
        MeetingInviteMessages.SUCCESS.get(lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title)
    )

    confirm_context.api.assert_edit_message_called(confirm_context.get_update(), expected_view)

    # The meeting now has one invited user, alongside whatever membership the inviter already had
    invited_links = [link for link in meeting.joined_links if link.invited_by is not None]
    assert len(invited_links) == 1
    invited_link = invited_links[0]
    assert invited_link.user.first_name == "Bruce Wayne"
    assert invited_link.invited_by is not None
    assert invited_link.invited_by.id == user_with_settings.id


async def test_invite_confirm_loads_meeting_with_row_lock(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
):
    """Wiring guard for the per-meeting mutex (#187): only the confirm step must lock the meeting —
    the earlier steps pre-validate unlocked so the lock is never held across the user's typing. The
    serialization behavior is covered on real Postgres in
    tests/models/db_behavior/test_meeting_row_locks.py; this only pins the call site."""
    setup_db(mock_session, user_with_settings, meeting)

    def _isolate_confirm_queries():
        # The earlier steps read the meeting unlocked by design; drop their statements so the
        # assertion below only sees the confirm step's queries.
        mock_session.exec.reset_mock()

    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message(
            "Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION, after=_isolate_confirm_queries
        ),
        ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
    ]

    await conversation.run(handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION, steps=steps)

    assert_locked_meetup_select(mock_session)


async def test_concurrent_duplicate_invitation_is_idempotent_noop(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
):
    """When confirming an invitation collides with the (user_id, meetup_id) unique constraint, the
    handler reports "already joined" and ends the conversation instead of emitting a fault."""
    setup_db(mock_session, user_with_settings, meeting)

    def _arm_clash():
        # The confirm step builds the membership row inside racy_flush's savepoint; make its
        # flush raise the uniqueness violation as if a concurrent update already registered
        # the participant.
        mock_session.flush.side_effect = integrity_error(JOINED_USERS_UNIQUE_CONSTRAINT)

    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION, after=_arm_clash),
        ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    confirm_context = result.last_context

    # The inviter is told the user is already joined, and the conversation ends.
    confirm_context.api.assert_answer_callback_query_called(
        update=confirm_context.get_update(),
        text=MeetingJoinMessages.JOIN_ALREADY_JOINED.get(lang=user_with_settings.lang),
        show_alert=True,
    )
    assert result.last_state is None

    # The clash is a handled no-op: no success message is edited in, and the conversation data is cleared.
    confirm_context.api.assert_method_just_called("edit_message", times=0)
    assert confirm_context.user_data is not None
    assert len(confirm_context.user_data.registry) == 0

    # No fault emitted (the MEETING_FULL fault branch was not taken) and no membership metric counted.
    confirm_metrics = MetricAssertions(confirm_context.metrics)
    confirm_metrics.assert_not_emitted(name=MetricKey.FAULT, value=1.0)
    confirm_metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.JOIN_MEETING)})


@pytest.mark.parametrize(
    "owner_id",
    [123, 456],
    ids=["by_owner", "by_other_user"],
)
async def test_invite_user_decline_confirmation(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    owner_id: int,
    meeting: Meetup,
):
    setup_inviter(mock_session, user_with_settings, meeting, owner_id)

    # These are the steps for the conversation when declining the confirmation
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
        ConversationStep.callback(cb.CANCEL_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    cancel_context = result.last_context

    message = MeetingInviteMessages.CANCELED.get(lang=user_with_settings.lang)

    if owner_id == 123:
        expected_view = meeting_views.view_for(meeting, user_with_settings).with_context(message)
    else:
        expected_view = views.factory.main_menu_view(
            RenderContext(lang=user_with_settings.lang),
            message=message,
        )

    cancel_context.api.assert_edit_message_called(
        update=cancel_context.get_update(),
        view=expected_view,
    )

    # The meeting has no invited users
    assert [link for link in meeting.joined_links if link.invited_by is not None] == []
    # The user data has been cleared
    assert cancel_context.user_data is not None
    assert len(cancel_context.user_data.registry) == 0


async def test_invite_user_adds_to_the_waiting_list(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
):
    setup_db(mock_session, user_with_settings, meeting)
    meeting.max_members = 1  # Only one member allowed
    meeting.waiting_list = True

    # First, add a participant to fill the meeting
    first_participant = create_user(id=200, tg_user_id=200, first_name="First Participant")
    mock_session.add_user(first_participant)
    meeting.add_participant(first_participant, invited_by=user_with_settings)

    # These are the steps for the conversation of inviting a user who should go to the waiting list
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
        ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    confirm_context = result.last_context

    # User has been sent confirmation of the invitation to the waiting list
    expected_view = meeting_views.main_view(meeting).with_context(
        MeetingInviteMessages.SUCCESS.get(lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title)
    )

    confirm_context.api.assert_edit_message_called(confirm_context.get_update(), expected_view)

    # The meeting now has one invited user in the waiting list
    assert len(meeting.joined_links) == 2
    invited_link = meeting.joined_links[1]
    assert invited_link.user.first_name == "Bruce Wayne"
    assert invited_link.invited_by is not None
    assert invited_link.invited_by.id == user_with_settings.id
    assert invited_link.is_waiting_list


def fill_meeting(meeting: Meetup):
    meeting.max_members = 0


def disable_invitations(meeting: Meetup):
    meeting.allow_invitation = False


def deacivate_meeting(meeting: Meetup):
    meeting.active = False


@pytest.mark.parametrize(
    "steps, after_callbacks, expected_message",
    [
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne"),
            ],
            [fill_meeting, None],
            MeetingInviteMessages.MEETING_FULL,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne"),
            ],
            [disable_invitations, None],
            MeetingInviteMessages.INVITES_DISABLED,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
            ],
            [None, fill_meeting, None],
            MeetingInviteMessages.MEETING_FULL,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
            ],
            [None, disable_invitations, None],
            MeetingInviteMessages.INVITES_DISABLED,
        ],
    ],
    ids=[
        "full_on_name_entry",
        "disabled_on_name_entry",
        "full_on_confirmation",
        "disabled_on_confirmation",
    ],
)
async def test_meeting_does_not_accept_invitations_after_conversation_started(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
    steps: list[ConversationStep],
    after_callbacks: list[None | Callable[[Meetup], None]],
    expected_message: MeetingInviteMessages,
):
    setup_db(mock_session, user_with_settings, meeting)

    # Prepare the after callbacks in the steps
    for i, after in enumerate(after_callbacks):
        if after is not None:
            steps[i].after = partial(after, meeting)

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    # In all these cases the user should have been sent to the main menu with the expected message
    final_context = result.last_context
    expected_view = views.factory.main_menu_view(RenderContext(lang=user_with_settings.lang))

    final_context.api.assert_answer_callback_query_called(
        update=final_context.get_update(),
        text=expected_message.get(lang=user_with_settings.lang),
        show_alert=True,
    )

    final_context.api.assert_edit_message_called(
        update=final_context.get_update(),
        view=expected_view,
    )

    assert len(meeting.joined_links) == 0
    assert final_context.user_data is not None
    assert len(final_context.user_data.registry) == 0


@pytest.mark.parametrize(
    "steps, deactivate_after_step",
    [
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                # The rejected step ends the flow, so PTB holds no state for the caller any more.
                ConversationStep.message("Bruce Wayne", expected_state=None),
            ],
            0,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID), expected_state=None),
            ],
            1,
        ],
    ],
    ids=["inactive_on_name_entry", "inactive_on_confirmation"],
)
async def test_meeting_disappears_mid_conversation(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
    steps: list[ConversationStep],
    deactivate_after_step: int,
):
    """A meeting that stops existing while the invite flow runs is answered by the deleted-meeting screen.

    The remaining steps read the meeting id back from the caller's own conversation state, so the
    rejection is the bot-chat one rather than the stale-card banner: a message step has no message of
    ours to replace and gets a fresh reply, the confirmation callback replaces the screen it sits on.

    The rejection aborts the step and the invite flow ends with it — what every guard exception does.
    The screen it lands on navigates out of the flow, so no state may keep claiming the caller's
    next messages; PTB drops the conversation key, which the tester reads back as no state at all.

    The name step opts into the flow context, so its screen also names the invite the user was in the
    middle of; the confirmation step does not, and gets the plain screen.
    """
    setup_db(mock_session, user_with_settings, meeting)
    steps[deactivate_after_step].after = partial(deacivate_meeting, meeting)

    result = await conversation.run(handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION, steps=steps)

    final_context = result.last_context
    update = final_context.get_update()
    lang = user_with_settings.lang
    expected_view = views_factory.deleted_meeting_view(RenderContext(lang=lang))
    if update.callback_query is not None:
        final_context.api.assert_edit_message_called(update=update, view=expected_view)
    else:
        final_context.api.assert_send_message_called(
            update=update,
            view=expected_view.with_footnote(MeetingInviteMessages.FLOW_CONTEXT.get(lang=lang)),
        )

    assert len(meeting.joined_links) == 0


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID), from_bot_chat=False),
    ],
    indirect=True,
)
async def test_invite_on_finished_meeting_replaces_the_card(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
):
    """A card whose meeting has finished is replaced by the finished banner, as join and leave do."""
    setup_db(mock_session, user_with_settings, meeting)
    deacivate_meeting(meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update=handler_context.update,
        view=MitupView(description=MeetingDisplayMessages.FINISHED_BANNER.get(lang=meeting.lang), keyboard=[]),
    )


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID), from_bot_chat=False),
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    "meeting_modifier, expected_message",
    [
        [disable_invitations, MeetingInviteMessages.INVITES_DISABLED],
        [fill_meeting, MeetingInviteMessages.MEETING_FULL],
    ],
    ids=[
        "invitations_disabled",
        "meeting_full",
    ],
)
async def test_meeting_not_allowing_invitations_on_callback_query(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
    meeting_modifier: Callable[[Meetup], None],
    expected_message: MeetingInviteMessages,
):
    setup_db(mock_session, user_with_settings, meeting)
    meeting_modifier(meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=expected_message.get(lang=user_with_settings.lang),
        show_alert=True,
    )


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_INVITE_USER.with_id(MEETING_ID))],
    indirect=True,
)
@pytest.mark.parametrize(
    "owner_id",
    [123, 456],
    ids=["by_owner", "by_other_user"],
)
@pytest.mark.parametrize(
    "handler_id",
    [
        MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK,
        MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK,
    ],
    ids=["cancel", "decline"],
)
async def test_abort_invitation_when_meeting_no_longer_allows_invitations(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
    handler_id: MeetingHandlerId,
    owner_id: int,
):
    """When the meeting can no longer be invited into, even an owner lands on the main menu.

    Both the NAME-step Cancel and the CONFIRMATION-step Decline share ``abort_invitation``,
    so both entry points must fall back to the main menu when the meeting is gone — even for the
    acting user who owns the meeting, which is what makes the ``by_owner`` case load-bearing.
    """
    if owner_id == 123:
        owner = user_with_settings
    else:
        owner = create_user(id=owner_id, tg_user_id=owner_id, first_name="Other owner")
        mock_session.add_user(user_with_settings)
    setup_db(mock_session, owner, meeting)
    deacivate_meeting(meeting)

    # Make the "even an owner" claim explicit rather than incidental: the by_owner case must
    # genuinely own the meeting, and the by_other_user case must not.
    assert (user_with_settings.own_meeting(MEETING_ID) is not None) is (owner_id == 123)

    context, _ = await call_handler(handler_id, handler_context=handler_context)

    expected_view = views_factory.main_menu_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingInviteMessages.CANCELED.get(lang=user_with_settings.lang),
    )
    context.api.assert_edit_message_called(handler_context.update, expected_view)


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(callback_query=cb.CANCEL_INVITE_USER.with_id(MEETING_ID))],
    indirect=True,
)
async def test_fallback_invite_user_clears_context_and_sends_main_menu(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
):
    """Fallback handler clears context, sends main menu with unexpected-update message, emits a FAULT metric."""
    setup_db(mock_session, user_with_settings, meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_FALLBACK, handler_context=handler_context)

    # Context data should have been cleared
    assert context.user_data is not None
    assert len(context.user_data.registry) == 0

    # Main menu should have been sent with the unexpected-updates message
    expected_view = views_factory.main_menu_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingInviteMessages.ADD_FAILED_RETRY.get(lang=user_with_settings.lang),
    )
    context.api.assert_send_message_to_user_called(user_with_settings, expected_view)

    # The FAULT metric should have been emitted with the expected prefix,
    # batched together with the other standard handler metrics in a single log line.
    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("FallbackInviteUserConversation"), value=1.0)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)
