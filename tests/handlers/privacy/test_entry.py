import pytest
from telegram import Update

from mitup_bot.handlers.privacy.enums import PrivacyHandlerId
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView, RenderContext, factory
from tests.helpers import HandlerContext, UpdateRequest, call_handler
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_PRIVACY)], indirect=True)
async def test_show_privacy_renders_the_privacy_screen(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.SHOW, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.privacy_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_USER_DATA)], indirect=True)
async def test_delete_data_shows_the_consequences_warning(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.DELETE_DATA, handler_context=handler_context)

    expected_view = factory.confirmation_view(
        RenderContext(lang=user_with_settings.lang),
        message=PrivacyMessages.DELETE_WARNING.get(lang=user_with_settings.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    context.api.assert_edit_message_called(update, expected_view)
    assert user_with_settings.status is UserStatus.MEMBER


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA)], indirect=True)
async def test_first_confirmation_shows_the_last_chance_prompt(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.CONFIRM_DELETE_DATA, handler_context=handler_context)

    expected_view = factory.confirmation_view(
        RenderContext(lang=user_with_settings.lang),
        message=PrivacyMessages.DELETE_LAST_CHANCE.get(lang=user_with_settings.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    context.api.assert_edit_message_called(update, expected_view)
    # Nothing is marked until the final confirmation.
    assert user_with_settings.status is UserStatus.MEMBER


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA_FINAL)], indirect=True)
async def test_final_confirmation_marks_the_user_for_deletion(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert user_with_settings.status is UserStatus.MEMBER

    context, _ = await call_handler(PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL, handler_context=handler_context)

    assert user_with_settings.status is UserStatus.DELETION_REQUESTED
    expected_view = MitupView(
        description=PrivacyMessages.DELETION_MARKED.get(lang=user_with_settings.lang), keyboard=[]
    )
    context.api.assert_edit_message_called(update, expected_view)
    metrics.assert_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.DATA_DELETION_REQUESTED)})


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_USER_DATA)], indirect=True)
async def test_decline_returns_to_the_privacy_screen_without_marking(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(PrivacyHandlerId.DECLINE_DELETE_DATA, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.privacy_view(RenderContext(lang=user_with_settings.lang)))
    assert user_with_settings.status is UserStatus.MEMBER
