import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, patreon_link, supporter
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.views.collaborate import patreon_unlink_confirmation_view

from .enums import CollaborateHandlerId
from .utils import build_collaborate_view, subscription_for_user

log = structlog.get_logger(__name__)

# The row is deleted, so this line is the only surviving evidence of why a user lost their tier —
# the competing cause, a Patreon webhook downgrade, is recorded on its own trail.
UNLINK_EVENT = "Patreon unlink confirmed"


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
    if subscription is None:
        # The confirmation is shown either way, so nothing else would record that a user reached
        # this screen with no row to delete; a level above the granted floor here is a real data
        # inconsistency.
        log.info(
            UNLINK_EVENT,
            user_id=user.db_id,
            stage="unlink",
            outcome="noop",
            reason="no_subscription_row",
            supporter_level=user.supporter_level.value,
            granted_level=user.granted_supporter_level.value,
        )
    else:
        # Read off the row before it is deleted: once the delete flushes, the instance is gone and
        # these are the only facts that explain the tier loss afterwards.
        previous_level = user.supporter_level
        patreon_user_id = subscription.patreon_user_id
        expiration = subscription.support_expiration
        await session.delete(subscription)
        user.supporter_level = user.granted_supporter_level
        # Deleting the row removes this user from every daily sweep, so hosts-group membership has
        # to be reconciled here or never: eject a member whose level just dropped below supporter,
        # and clear any ban a past revoke left, since no sweep can ever lift it once the row is
        # gone. A user whose granted floor keeps them a supporter keeps their group access.
        if not supporter.is_supporter(user.supporter_level):
            await patreon_link.withdraw_from_hosts_group(context.api, user)
        log.info(
            UNLINK_EVENT,
            user_id=user.db_id,
            patreon_user_id=patreon_user_id,
            stage="unlink",
            outcome="unlinked",
            reason="user_requested",
            previous_supporter_level=previous_level.value,
            supporter_level=user.supporter_level.value,
            support_expiration=expiration.isoformat() if expiration is not None else None,
        )

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
