from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, patreon_link
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.views.collaborate import patreon_unlink_confirmation_view

from .enums import CollaborateHandlerId
from .utils import build_collaborate_view, subscription_for_user


@HandlersRegistry.register_callback_query(CollaborateHandlerId.SHOW, callback_data=cb.COLLABORATE, bindable=True)
@with_session
async def callback_query_collaborate(session: AsyncSession, update: Update, context: TMitupContext):
    # `build_collaborate_view` reads `user.lang`/`user.id`/`user.tg_user_id`/`user.supporter_level`
    # only (never the meetups/joined_links collections), so skip loading them.
    user = await guards.current_user(update, session)
    view = await build_collaborate_view(session, user, context)
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(CollaborateHandlerId.UNLINK, callback_data=cb.UNLINK_PATREON, bindable=True)
@with_session
async def callback_query_unlink_patreon(session: AsyncSession, update: Update, context: TMitupContext):
    """Open the unlink confirmation prompt. Nothing is deleted until it is confirmed.

    Unlinking a Host also switches their perks off and ends their hosts-group access, so a single
    tap must not be enough: the prompt spells out what confirming costs, in the variant matching
    the tier the user holds right now.
    """
    user = await guards.current_user(update, session)

    subscription = await subscription_for_user(session, user)
    if subscription is None:
        # A stale button: nothing is linked, so there is nothing to confirm — show the current
        # screen instead of a prompt about a connection that does not exist.
        await context.api.edit_message(update=update, view=await build_collaborate_view(session, user, context))
        return

    view = patreon_unlink_confirmation_view(
        guards.render_context(user, update, context), current_level=user.supporter_level
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    CollaborateHandlerId.UNLINK_CONFIRM, callback_data=cb.CONFIRM_PATREON_UNLINK, bindable=True
)
@with_session(write=True)
async def callback_query_confirm_unlink_patreon(session: AsyncSession, update: Update, context: TMitupContext):
    # Reads/writes `user.supporter_level` and passes the user to `subscription_for_user`/
    # `build_collaborate_view`, both of which touch only scalar columns — never the
    # meetups/joined_links collections, so skip loading them. Write mode: the group removal and its
    # DM must only run once the row deletion has committed.
    user = await guards.current_user(update, session)

    subscription = await subscription_for_user(session, user)
    if subscription is not None:
        await session.delete(subscription)
        user.supporter_level = SupporterLevel.NONE
        # Deleting the row removes this user from every daily sweep, so hosts-group membership has
        # to be reconciled here or never: eject a member whose level just dropped to NONE, and
        # clear any ban a past revoke left, since no sweep can ever lift it once the row is gone.
        await patreon_link.withdraw_from_hosts_group(context.api, user)

    # The pending delete flushes before build_collaborate_view re-reads the subscription, so the
    # view resolves to the not-linked state; the context line confirms the unlink above it.
    view = (await build_collaborate_view(session, user, context)).with_context(
        CollaborateMessages.UNLINKED.get(lang=user.lang)
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    CollaborateHandlerId.UNLINK_DECLINE, callback_data=cb.DECLINE_PATREON_UNLINK, bindable=True
)
@with_session
async def callback_query_decline_unlink_patreon(session: AsyncSession, update: Update, context: TMitupContext):
    """Back out of the unlink prompt: nothing was written, back to the Collaborate screen."""
    user = await guards.current_user(update, session)
    await context.api.edit_message(update=update, view=await build_collaborate_view(session, user, context))
