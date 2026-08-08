from typing import TYPE_CHECKING
from unittest import mock

import pytest
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot import supporter
from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.handlers.supporter_grant.enums import ConversationGrantState, GrantHandlerId
from mitup_bot.handlers.supporter_grant.utils import find_target, target_prompt_view, target_summary_view
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import GrantOperatorMessages, SupporterNotificationMessages
from mitup_bot.views import RenderContext, factory
from mitup_bot.views.collaborate import grant_notification_view
from tests.helpers import (
    ConversationStep,
    ConversationTester,
    HandlerContext,
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
    create_bot_config,
    create_settings,
    create_supporter_subscription,
    create_user,
)

if TYPE_CHECKING:
    from tests.helpers.types import RegisterGrantTarget, RegisterMember

TARGET_TG_ID = 555

# A handler returning ConversationHandler.END removes the stored conversation state, which the
# tester reads back as None.
END_STATE = None

GRANT_OPEN_UPDATE = pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SUPPORTER_GRANT)], indirect=True)


def make_target(
    *,
    level: SupporterLevel = SupporterLevel.NONE,
    granted: SupporterLevel = SupporterLevel.NONE,
    username: str | None = "targetuser",
) -> User:
    target = create_user(id=5, tg_user_id=TARGET_TG_ID, username=username, settings=create_settings(id=5))
    target.supporter_level = level
    target.granted_supporter_level = granted
    return target


# --- Entry ---


@GRANT_OPEN_UPDATE
async def test_grant_button_admin_opens_flow(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    context, state = await call_handler(GrantHandlerId.GRANT_OPEN_CALLBACK, handler_context=handler_context)

    assert state == ConversationGrantState.AWAITING_TARGET
    context.api.assert_edit_message_called(update, target_prompt_view(user_with_settings.lang))


@GRANT_OPEN_UPDATE
async def test_grant_button_non_admin_is_dropped(
    update: Update,
    handler_context: HandlerContext,
    app: StubMitupApp,
):
    """A forged callback from a non-admin never runs the entry: the feature stays invisible."""
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([999])

    context, state = await call_handler(GrantHandlerId.GRANT_OPEN_CALLBACK, handler_context=handler_context)

    assert state is None
    context.api.assert_edit_message_not_called()


def test_target_prompt_view_has_cancel_button():
    view = target_prompt_view("en")

    buttons = [button for row in view.keyboard for button in row]
    assert any(button.callback_data == cb.CANCEL_GRANT for button in buttons)


# --- Target resolution ---


@pytest.mark.parametrize("identifier", [str(TARGET_TG_ID), "@TargetUser", "targetuser"])
async def test_target_message_resolves_member_and_shows_picker(
    identifier: str,
    conversation: ConversationTester,
    user_with_settings: User,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    register_member(user_with_settings)
    target = make_target()
    register_target(target)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=identifier, expected_state=ConversationGrantState.AWAITING_LEVEL),
        ],
    )

    step = result.last_context
    step.api.assert_send_message_called(
        step.get_update(), target_summary_view(user_with_settings.lang, target, linked=False)
    )


async def test_target_message_unknown_identifier_reprompts(
    conversation: ConversationTester,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text="@nobody", expected_state=ConversationGrantState.AWAITING_TARGET),
        ],
    )

    step = result.last_context
    step.api.assert_send_message_called(
        step.get_update(),
        GrantOperatorMessages.TARGET_NOT_FOUND.get(lang=user_with_settings.lang, identifier="@nobody"),
    )


# --- Level pick and confirmation ---


async def test_level_pick_shows_confirmation(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.granted_supporter_level = SupporterLevel.NONE
    target = make_target()
    register_target(target)
    rank = supporter.rank(SupporterLevel.HOST_2)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=str(TARGET_TG_ID), expected_state=ConversationGrantState.AWAITING_LEVEL),
            ConversationStep.callback(
                data=cb.SET_GRANT_LEVEL.with_level(target.db_id, rank),
                expected_state=ConversationGrantState.AWAITING_CONFIRMATION,
            ),
        ],
    )

    step = result.last_context
    lang = user_with_settings.lang
    step.api.assert_edit_message_called(
        step.get_update(),
        factory.confirmation_view(
            RenderContext(lang=lang, is_admin=True),
            message=GrantOperatorMessages.CONFIRM_PROMPT.get(
                lang=lang,
                name=target.display_name,
                level=GrantOperatorMessages.level_label(SupporterLevel.HOST_2).get(lang=lang),
            ),
            confirm_callback_data=cb.CONFIRM_GRANT.with_level(target.db_id, rank),
            decline_callback_data=cb.CANCEL_GRANT,
        ),
    )


async def test_confirm_grants_the_level_to_an_unlinked_target(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")
    target = make_target()
    register_target(target)
    rank = supporter.rank(SupporterLevel.HOST_3)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=str(TARGET_TG_ID), expected_state=ConversationGrantState.AWAITING_LEVEL),
            ConversationStep.callback(
                data=cb.SET_GRANT_LEVEL.with_level(target.db_id, rank),
                expected_state=ConversationGrantState.AWAITING_CONFIRMATION,
            ),
            ConversationStep.callback(data=cb.CONFIRM_GRANT.with_level(target.db_id, rank), expected_state=END_STATE),
        ],
    )

    assert target.granted_supporter_level is SupporterLevel.HOST_3
    assert target.supporter_level is SupporterLevel.HOST_3
    step = result.last_context
    step.api.assert_send_message_to_user_called(
        user=target,
        view=grant_notification_view(
            SupporterNotificationMessages.granted_for(SupporterLevel.HOST_3).get(lang=target.lang), target.lang
        ),
    )


async def test_confirm_removing_the_grant_revokes_and_notifies(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")
    target = make_target(level=SupporterLevel.HOST_2, granted=SupporterLevel.HOST_2)
    register_target(target)
    rank = supporter.rank(SupporterLevel.NONE)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=str(TARGET_TG_ID), expected_state=ConversationGrantState.AWAITING_LEVEL),
            ConversationStep.callback(
                data=cb.SET_GRANT_LEVEL.with_level(target.db_id, rank),
                expected_state=ConversationGrantState.AWAITING_CONFIRMATION,
            ),
            ConversationStep.callback(data=cb.CONFIRM_GRANT.with_level(target.db_id, rank), expected_state=END_STATE),
        ],
    )

    assert target.granted_supporter_level is SupporterLevel.NONE
    assert target.supporter_level is SupporterLevel.NONE
    step = result.last_context
    step.api.assert_send_message_to_user_called(
        user=target,
        view=grant_notification_view(SupporterNotificationMessages.GRANT_REMOVED.get(lang=target.lang), target.lang),
    )


async def test_confirm_never_lowers_a_linked_targets_level(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    """Lowering the floor for a Patreon-linked user leaves the stored level for the daily
    reconciliation to settle, so a paying patron is never dropped below their entitlement here."""
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")
    target = make_target(level=SupporterLevel.HOST_3, granted=SupporterLevel.HOST_3)
    register_target(target)
    subscription = create_supporter_subscription(user_id=target.db_id, patreon_user_id="p-grant")
    mock_session.add_object(subscription, "user_id")
    rank = supporter.rank(SupporterLevel.HOST_1)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=str(TARGET_TG_ID), expected_state=ConversationGrantState.AWAITING_LEVEL),
            ConversationStep.callback(
                data=cb.SET_GRANT_LEVEL.with_level(target.db_id, rank),
                expected_state=ConversationGrantState.AWAITING_CONFIRMATION,
            ),
            ConversationStep.callback(data=cb.CONFIRM_GRANT.with_level(target.db_id, rank), expected_state=END_STATE),
        ],
    )

    assert target.granted_supporter_level is SupporterLevel.HOST_1
    assert target.supporter_level is SupporterLevel.HOST_3
    # Nothing user-visible changed, so the target gets no DM.
    result.last_context.api.assert_method_just_called("send_message_to_user", times=0)


async def test_cancel_returns_to_the_admin_menu(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
):
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.callback(data=cb.CANCEL_GRANT, expected_state=END_STATE),
        ],
    )

    step = result.last_context
    lang = user_with_settings.lang
    step.api.assert_edit_message_called(
        step.get_update(),
        factory.admin_menu_view(RenderContext(lang=lang, is_admin=True)).with_context(
            GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=lang)
        ),
    )


# --- Soft-refusal branches ---


@GRANT_OPEN_UPDATE
async def test_grant_button_admin_without_member_row_ends_silently(
    update: Update,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    """An admin id that resolves to no reachable member row ends the flow without a reply, rather
    than letting the operator load crash."""
    context, state = await call_handler(GrantHandlerId.GRANT_OPEN_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(message_text=str(TARGET_TG_ID))], indirect=True)
async def test_target_message_without_member_operator_is_ignored(
    update: Update,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    context, state = await call_handler(GrantHandlerId.GRANT_TARGET_MESSAGE, handler_context=handler_context)

    assert state == ConversationGrantState.AWAITING_TARGET
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))], indirect=True)
async def test_non_text_input_on_the_target_step_reprompts(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    context, state = await call_handler(GrantHandlerId.GRANT_INVALID_TARGET_MESSAGE, handler_context=handler_context)

    assert state == ConversationGrantState.AWAITING_TARGET
    context.api.assert_send_message_called(
        update, GrantOperatorMessages.TARGET_PROMPT.get(lang=user_with_settings.lang)
    )


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(longitude=1.0, latitude=2.0))], indirect=True)
async def test_non_text_input_without_member_operator_is_ignored(
    update: Update,
    handler_context: HandlerContext,
    mock_session: MockDbSession,
):
    context, state = await call_handler(GrantHandlerId.GRANT_INVALID_TARGET_MESSAGE, handler_context=handler_context)

    assert state == ConversationGrantState.AWAITING_TARGET
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "vanished_target",
    [None, "left_member"],
    ids=["row_gone", "no_longer_member"],
)
@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_GRANT_LEVEL.with_level(5, 1))], indirect=True)
async def test_level_pick_for_a_vanished_target_aborts_to_the_admin_menu(
    vanished_target: str | None,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    """A stale picker button whose target row is gone, or whose target left the bot in the
    meantime, abandons the flow back to the admin menu instead of granting anything."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    stale_row = None if vanished_target is None else make_target(username=None)
    if stale_row is not None:
        stale_row.status = UserStatus.LEFT
    mock_session.get = mock.AsyncMock(return_value=stale_row)

    context, state = await call_handler(GrantHandlerId.GRANT_LEVEL_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    lang = user_with_settings.lang
    context.api.assert_edit_message_called(
        update,
        factory.admin_menu_view(RenderContext(lang=lang, is_admin=True)).with_context(
            GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=lang)
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_GRANT.with_level(5, 1))], indirect=True)
async def test_confirm_for_a_vanished_target_grants_nothing(
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.get = mock.AsyncMock(return_value=None)

    context, state = await call_handler(GrantHandlerId.GRANT_CONFIRM_CALLBACK, handler_context=handler_context)

    assert state == ConversationHandler.END
    context.api.assert_method_just_called("send_message_to_user", times=0)
    lang = user_with_settings.lang
    context.api.assert_edit_message_called(
        update,
        factory.admin_menu_view(RenderContext(lang=lang, is_admin=True)).with_context(
            GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=lang)
        ),
    )


async def test_find_target_with_a_bare_at_sign_resolves_nobody(mock_session: MockDbSession):
    assert await find_target(mock_session, "@") is None


async def test_lowering_the_grant_notifies_the_new_tier(
    conversation: ConversationTester,
    user_with_settings: User,
    mock_session: MockDbSession,
    register_member: RegisterMember,
    register_target: RegisterGrantTarget,
):
    """Lowering an unlinked user's grant to a still-paying tier sends the neutral tier-set DM, not
    the grant-removed one."""
    register_member(user_with_settings)
    mock_session.add_object(user_with_settings, "tg_user_id")
    target = make_target(level=SupporterLevel.HOST_2, granted=SupporterLevel.HOST_2)
    register_target(target)
    rank = supporter.rank(SupporterLevel.HOST_1)

    result = await conversation.run(
        GrantHandlerId.GRANT_CONVERSATION,
        steps=[
            ConversationStep.callback(data=cb.SUPPORTER_GRANT, expected_state=ConversationGrantState.AWAITING_TARGET),
            ConversationStep.message(text=str(TARGET_TG_ID), expected_state=ConversationGrantState.AWAITING_LEVEL),
            ConversationStep.callback(
                data=cb.SET_GRANT_LEVEL.with_level(target.db_id, rank),
                expected_state=ConversationGrantState.AWAITING_CONFIRMATION,
            ),
            ConversationStep.callback(data=cb.CONFIRM_GRANT.with_level(target.db_id, rank), expected_state=END_STATE),
        ],
    )

    assert target.granted_supporter_level is SupporterLevel.HOST_1
    assert target.supporter_level is SupporterLevel.HOST_1
    result.last_context.api.assert_send_message_to_user_called(
        user=target,
        view=grant_notification_view(
            SupporterNotificationMessages.downgraded_to(SupporterLevel.HOST_1).get(lang=target.lang), target.lang
        ),
    )
