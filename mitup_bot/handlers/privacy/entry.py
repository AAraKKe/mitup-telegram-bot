from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import MitupView, factory

from .enums import PrivacyHandlerId


@HandlersRegistry.register_callback_query(PrivacyHandlerId.SHOW, callback_data=cb.EDIT_PRIVACY)
@with_session
async def callback_query_show_privacy(session: AsyncSession, update: Update, context: TMitupContext):
    # Privacy screens read only `user.lang`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session, load_collections=False)
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )


@HandlersRegistry.register_callback_query(PrivacyHandlerId.DELETE_DATA, callback_data=cb.DELETE_USER_DATA)
@with_session
async def callback_query_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_WARNING.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.CONFIRM_DELETE_DATA, callback_data=cb.CONFIRM_DELETE_USER_DATA
)
@with_session
async def callback_query_confirm_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_LAST_CHANCE.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL, callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL
)
@with_session
async def callback_query_confirm_delete_user_data_final(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)
    user.status = UserStatus.DELETION_REQUESTED
    context.put_feature_metric(Feature.DATA_DELETION_REQUESTED)
    # No buttons: the account has stopped working, so there is no screen left to navigate to.
    view = MitupView(description=PrivacyMessages.DELETION_MARKED.get(lang=user.lang), keyboard=[])
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.DECLINE_DELETE_DATA, callback_data=cb.DECLINE_DELETE_USER_DATA
)
@with_session
async def callback_query_decline_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session, load_collections=False)
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )
