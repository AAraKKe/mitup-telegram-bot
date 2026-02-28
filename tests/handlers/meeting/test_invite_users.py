from collections.abc import Callable
from functools import partial

import pytest

from mitup_bot import views
from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.meeting.enums import ConversationInviteState, MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory as views_factory
from tests.helpers import MockDbSession, UpdateRequest, call_handler, create_meetup, create_user
from tests.helpers.conversation import ConversationStep, ConversationTester
from tests.helpers.handler_context import HandlerContext

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
        lang=user_with_settings.lang,
        message=MeetingMessages.INVITE_USER_PROMPT.get(lang=user_with_settings.lang),
        callback_data=cb.DECLINE_INVITE_USER.with_id(MEETING_ID),
    )

    if external_chat:
        context.api.assert_answer_callback_query_called(
            handler_context.update,
            text=MeetingMessages.INVITE_USER_GO_PRIVATE.get(lang=user_with_settings.lang, plain=True),
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
        text=MeetingMessages.INVITE_USER_OPEN_CHAT.get(lang=user_with_settings.lang),
        show_alert=True,
    )


async def test_invite_with_id_of_meeting_does_not_exist(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_user(user_with_settings)
    # Should return None and end conversation and answer with alert saying that the meeting no longer exists
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    result.last_context.api.assert_answer_callback_query_called(
        update=result.last_context.get_update(),
        text=MeetingMessages.INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK.get(lang=user_with_settings.lang, plain=True),
        show_alert=True,
    )


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
        lang=user_with_settings.lang,
        message=MeetingMessages.INVITE_USER_CONFIRMATION.get(
            lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title
        ),
        confirm_callback_data=cb.CONFIRM_INVITE_USER.with_id(MEETING_ID),
        decline_callback_data=cb.DECLINE_INVITE_USER.with_id(MEETING_ID),
    )

    message_step.context.api.assert_send_message_to_user_called(
        user_with_settings,
        expected_view,
    )

    # The name has been stored in the user data
    with message_step.context.text(ContextId.INVITE_USERS) as text:
        assert text == "Bruce Wayne"


async def test_cancel_name_request(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
):
    setup_db(mock_session, user_with_settings, meeting)

    # These are the steps for the conversation when cancelling the name request
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.callback(cb.DECLINE_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    callback_step = result.get_step(0)
    cancel_step = result.get_step(1)

    assert callback_step.state is ConversationInviteState.NAME
    assert cancel_step.state is None  # Conversation has ended

    # User has been sent back to the main menu
    expected_view = views.factory.main_menu_view(
        lang=user_with_settings.lang,
        message=MeetingMessages.INVITE_USERS_CANCELED.get(lang=user_with_settings.lang),
    )

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
    if owner_id == 123:
        owner = user_with_settings
    else:
        owner = create_user(id=owner_id, tg_user_id=owner_id, first_name="Other owner")
        mock_session.add_user(user_with_settings)
    setup_db(mock_session, owner, meeting)

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
    expected_view = meeting.main_view if owner_id == 123 else meeting.external_view
    expected_view = expected_view.with_context(
        MeetingMessages.INVITE_USER_SUCCESS.get(
            lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title
        )
    )

    confirm_context.api.assert_edit_message_called(confirm_context.get_update(), expected_view)

    # The meeting now has one invited user
    assert len(meeting.joined_links) == 1
    invited_link = meeting.joined_links[0]
    assert invited_link.user.first_name == "Bruce Wayne"
    assert invited_link.invited_by is not None
    assert invited_link.invited_by.id == user_with_settings.id


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
    if owner_id == 123:
        owner = user_with_settings
    else:
        owner = create_user(id=owner_id, tg_user_id=owner_id, first_name="Other owner")
        mock_session.add_user(user_with_settings)
    setup_db(mock_session, owner, meeting)

    # These are the steps for the conversation when declining the confirmation
    steps = [
        ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
        ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
        ConversationStep.callback(cb.DECLINE_INVITE_USER.with_id(MEETING_ID)),
    ]

    result = await conversation.run(
        handler_id=MeetingHandlerId.INVITE_USERS_CONVERSATION,
        steps=steps,
    )

    cancel_context = result.last_context

    message = MeetingMessages.INVITE_USERS_CANCELED.get(lang=user_with_settings.lang)

    if owner_id == 123:
        expected_view = meeting.view_for(user_with_settings).with_context(message)
    else:
        expected_view = views.factory.main_menu_view(
            lang=user_with_settings.lang,
            message=message,
        )

    cancel_context.api.assert_edit_message_called(
        update=cancel_context.get_update(),
        view=expected_view,
    )

    # The meeting has no invited users
    assert len(meeting.joined_links) == 0
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
    expected_view = meeting.main_view.with_context(
        MeetingMessages.INVITE_USER_SUCCESS.get(
            lang=user_with_settings.lang, name="Bruce Wayne", meeting_title=meeting.title
        )
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
            MeetingMessages.INVITE_USER_MEETING_FULL,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne"),
            ],
            [disable_invitations, None],
            MeetingMessages.INVITE_USER_INVITES_DISABLED,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
            ],
            [None, fill_meeting, None],
            MeetingMessages.INVITE_USER_MEETING_FULL,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
            ],
            [None, disable_invitations, None],
            MeetingMessages.INVITE_USER_INVITES_DISABLED,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne"),
            ],
            [deacivate_meeting, None],
            MeetingMessages.INVITE_USERS_MEETING_NOT_FOUND,
        ],
        [
            [
                ConversationStep.callback(cb.INVITE.with_id(MEETING_ID), expected_state=ConversationInviteState.NAME),
                ConversationStep.message("Bruce Wayne", expected_state=ConversationInviteState.CONFIRMATION),
                ConversationStep.callback(cb.CONFIRM_INVITE_USER.with_id(MEETING_ID)),
            ],
            [None, deacivate_meeting, None],
            MeetingMessages.INVITE_USERS_MEETING_NOT_FOUND,
        ],
    ],
    ids=[
        "full_on_name_entry",
        "disabled_on_name_entry",
        "full_on_confirmation",
        "disabled_on_confirmation",
        "inactive_on_name_entry",
        "inactive_on_confirmation",
    ],
)
async def test_meeting_does_not_accept_invitations_after_conversation_started(
    mock_session: MockDbSession,
    user_with_settings: User,
    conversation: ConversationTester,
    meeting: Meetup,
    steps: list[ConversationStep],
    after_callbacks: list[None | Callable[[Meetup], None]],
    expected_message: MeetingMessages,
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
    expected_view = views.factory.main_menu_view(lang=user_with_settings.lang)

    final_context.api.assert_answer_callback_query_called(
        update=final_context.get_update(),
        text=expected_message.get(lang=user_with_settings.lang, plain=True),
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
    "update",
    [
        UpdateRequest(callback_query=cb.INVITE.with_id(MEETING_ID), from_bot_chat=False),
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    "meeting_modifier, expected_message",
    [
        [disable_invitations, MeetingMessages.INVITE_USER_INVITES_DISABLED],
        [fill_meeting, MeetingMessages.INVITE_USER_MEETING_FULL],
        [deacivate_meeting, MeetingMessages.INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK],
    ],
    ids=[
        "invitations_disabled",
        "meeting_full",
        "meeting_inactive",
    ],
)
async def test_meeting_not_allowing_invitations_on_callback_query(
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    meeting: Meetup,
    meeting_modifier: Callable[[Meetup], None],
    expected_message: MeetingMessages,
):
    setup_db(mock_session, user_with_settings, meeting)
    meeting_modifier(meeting)

    context, _ = await call_handler(MeetingHandlerId.INVITE_USERS_CONVERSATION, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update=handler_context.update,
        text=expected_message.get(lang=user_with_settings.lang, plain=True),
        show_alert=True,
    )
