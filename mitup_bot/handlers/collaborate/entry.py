from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.utils.mitup_types import TMitupContext

from .enums import CollaborateHandlerId
from .utils import build_collaborate_view, subscription_for_user, tapped_message_id


@HandlersRegistry.register_callback_query(CollaborateHandlerId.SHOW, callback_data=cb.COLLABORATE, bindable=True)
@with_session
async def callback_query_collaborate(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    # The tapped message is edited in place below; carry its id through the OAuth state so the web
    # callback can refresh this same message into the linked view after linking.
    view = await build_collaborate_view(session, user, tapped_message_id(update))
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(CollaborateHandlerId.UNLINK, callback_data=cb.UNLINK_PATREON, bindable=True)
@with_session
async def callback_query_unlink_patreon(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    subscription = await subscription_for_user(session, user)
    if subscription is not None:
        await session.delete(subscription)
        user.supporter_level = SupporterLevel.NONE

    # The pending delete flushes before build_collaborate_view re-reads the subscription, so the
    # view resolves to the not-linked state; the context line confirms the unlink above it. The
    # tapped message id is threaded through the fresh OAuth state so a later re-link can refresh it.
    view = (await build_collaborate_view(session, user, tapped_message_id(update))).with_context(
        CollaborateMessages.UNLINKED.get(lang=user.lang)
    )
    await context.api.edit_message(update=update, view=view)
