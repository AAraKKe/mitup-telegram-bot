import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import GrantOperatorMessages

from .enums import ConversationGrantState, GrantHandlerId
from .utils import apply_grant, load_target, picked_level

log = structlog.get_logger(__name__)

# A level or confirm tap whose target row disappeared or left in the meantime, reported under one
# name by both callback handlers.
TARGET_UNUSABLE_EVENT = "Supporter grant target unusable"


@HandlersRegistry.register_callback_query(
    GrantHandlerId.GRANT_LEVEL_CALLBACK, callback_data=cb.SET_GRANT_LEVEL, bindable=False, admin_only=True
)
@with_session
async def callback_query_pick_grant_level(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationGrantState | int:
    valid = guards.valid_grant_callback_data(
        cb.SET_GRANT_LEVEL.parse(context.match), GrantHandlerId.GRANT_LEVEL_CALLBACK
    )
    level = picked_level(valid, GrantHandlerId.GRANT_LEVEL_CALLBACK)
    # Reads only `operator.lang` and the render context; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session)

    target = await load_target(session, valid.id)
    if target is None:
        log.warning(
            TARGET_UNUSABLE_EVENT, stage="level", outcome="aborted", reason="target_not_member", target_user_id=valid.id
        )
        await show_admin_menu(
            update, context, operator, GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=operator.lang)
        )
        return ConversationHandler.END

    view = views.factory.confirmation_view(
        guards.render_context(operator, update, context),
        message=GrantOperatorMessages.CONFIRM_PROMPT.get(
            lang=operator.lang,
            name=target.display_name,
            level=GrantOperatorMessages.level_label(level).get(lang=operator.lang),
        ),
        confirm_callback_data=cb.CONFIRM_GRANT.with_level(valid.id, valid.level),
        decline_callback_data=cb.CANCEL_GRANT,
    )
    await context.api.edit_message(update=update, view=view)
    log.info(
        "Supporter grant confirmation shown",
        stage="level",
        outcome="awaiting_confirmation",
        target_user_id=target.db_id,
        granted_level=level.value,
    )
    return ConversationGrantState.AWAITING_CONFIRMATION


@HandlersRegistry.register_callback_query(
    GrantHandlerId.GRANT_CONFIRM_CALLBACK, callback_data=cb.CONFIRM_GRANT, bindable=False, admin_only=True
)
@with_session(write=True)
async def callback_query_confirm_grant(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    valid = guards.valid_grant_callback_data(
        cb.CONFIRM_GRANT.parse(context.match), GrantHandlerId.GRANT_CONFIRM_CALLBACK
    )
    level = picked_level(valid, GrantHandlerId.GRANT_CONFIRM_CALLBACK)
    # Reads only `operator.lang` and the render context; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session)

    target = await load_target(session, valid.id)
    if target is None:
        log.warning(
            TARGET_UNUSABLE_EVENT,
            stage="confirm",
            outcome="aborted",
            reason="target_not_member",
            target_user_id=valid.id,
        )
        await show_admin_menu(
            update, context, operator, GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=operator.lang)
        )
        return ConversationHandler.END

    outcome = await apply_grant(session, context.api, target, level)
    await show_admin_menu(
        update,
        context,
        operator,
        GrantOperatorMessages.APPLIED_CONFIRMATION.get(
            lang=operator.lang,
            name=target.display_name,
            level=GrantOperatorMessages.level_label(level).get(lang=operator.lang),
        ),
    )
    # The authorisation trail of a manual tier change: who granted what to whom, from where to
    # where. The ambient bind names the acting admin.
    log.info(
        "Supporter grant applied",
        stage="confirm",
        outcome="applied",
        target_user_id=target.db_id,
        target_tg_user_id=target.tg_user_id,
        previous_level=outcome.previous_level.value,
        new_level=outcome.new_level.value,
        previous_granted_level=outcome.previous_granted_level.value,
        granted_level=level.value,
        patreon_linked=outcome.linked,
    )
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    GrantHandlerId.GRANT_CANCEL_CALLBACK, callback_data=cb.CANCEL_GRANT, bindable=False, admin_only=True
)
@with_session
async def callback_query_cancel_grant(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    # Reads only `operator.lang` and the render context; never traverses the meetups/joined_links
    # collections.
    operator = await guards.current_user(update, session)
    await show_admin_menu(
        update, context, operator, GrantOperatorMessages.CANCELLED_CONFIRMATION.get(lang=operator.lang)
    )
    log.info("Supporter grant flow cancelled", user_id=operator.db_id, stage="cancel", outcome="abandoned")
    return ConversationHandler.END


async def show_admin_menu(update: Update, context: TMitupContext, operator: User, message: FormattedText):
    """Return to the admin menu with the flow's outcome prepended, so the operator both reads how
    the flow ended and keeps the admin-menu keyboard."""
    await context.api.edit_message(
        update=update,
        view=views.factory.admin_menu_view(guards.render_context(operator, update, context)).with_context(message),
    )
