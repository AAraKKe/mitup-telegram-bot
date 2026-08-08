import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .enums import ConversationGrantState, GrantHandlerId
from .utils import target_prompt_view

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    GrantHandlerId.GRANT_OPEN_CALLBACK, callback_data=cb.SUPPORTER_GRANT, bindable=False, admin_only=True
)
@with_session
async def callback_query_open_supporter_grant(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationGrantState | int:
    # The registry gate (admin_only) already vetted the admin; member_user only resolves the row
    # for the operator's language. A missing member row (an admin id that is not a registered
    # member) bails silently rather than letting current_user raise UserNotFound.
    operator = await guards.member_user(update, session)
    if operator is None:
        # The admin taps Host grants and the screen does not change; nothing else explains it.
        log.warning(
            "Supporter grant flow not opened", stage="entry", outcome="aborted", reason="admin_without_member_user"
        )
        return ConversationHandler.END

    await context.api.edit_message(update=update, view=target_prompt_view(operator.lang))
    # The anchor of the audit trail: who opened a grant flow, and when.
    log.info("Supporter grant flow opened", user_id=operator.db_id, stage="entry", outcome="awaiting_target")
    return ConversationGrantState.AWAITING_TARGET
