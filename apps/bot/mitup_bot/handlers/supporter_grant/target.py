import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import filters

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils.messages import GrantOperatorMessages

from .enums import ConversationGrantState, GrantHandlerId
from .utils import find_target, patreon_linked, target_summary_view

log = structlog.get_logger(__name__)

# An admin id with no MEMBER row reaches both target handlers and is answered by neither, so both
# report the drop under one name.
TARGET_IGNORED_EVENT = "Supporter grant target input ignored"


@HandlersRegistry.register_message(
    GrantHandlerId.GRANT_TARGET_MESSAGE, filters.TEXT & ~filters.COMMAND, bindable=False, admin_only=True
)
@with_session
async def grant_target_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationGrantState:
    operator = await guards.member_user(update, session)
    if operator is None:
        log.warning(TARGET_IGNORED_EVENT, stage="target", outcome="ignored", reason="operator_not_member_user")
        return ConversationGrantState.AWAITING_TARGET
    message = guards.message(update)
    identifier = (message.text or "").strip()

    target = await find_target(session, identifier)
    if target is None:
        log.info(
            "Supporter grant target not resolved",
            stage="target",
            outcome="reprompted",
            reason="no_matching_member",
            identifier_len=len(identifier),
        )
        await context.api.send_message(
            update=update, view=GrantOperatorMessages.TARGET_NOT_FOUND.get(lang=operator.lang, identifier=identifier)
        )
        return ConversationGrantState.AWAITING_TARGET

    linked = await patreon_linked(session, target)
    await context.api.send_message(update=update, view=target_summary_view(operator.lang, target, linked=linked))
    log.info(
        "Supporter grant target resolved",
        stage="target",
        outcome="awaiting_level",
        target_user_id=target.db_id,
        target_tg_user_id=target.tg_user_id,
        target_level=target.supporter_level.value,
        target_granted_level=target.granted_supporter_level.value,
        patreon_linked=linked,
    )
    return ConversationGrantState.AWAITING_LEVEL


@HandlersRegistry.register_message(
    GrantHandlerId.GRANT_INVALID_TARGET_MESSAGE, ~filters.COMMAND, bindable=False, admin_only=True
)
@with_session
async def grant_invalid_target_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationGrantState:
    operator = await guards.member_user(update, session)
    if operator is None:
        log.warning(TARGET_IGNORED_EVENT, stage="target", outcome="ignored", reason="operator_not_member_user")
        return ConversationGrantState.AWAITING_TARGET
    await context.api.send_message(update=update, view=GrantOperatorMessages.TARGET_PROMPT.get(lang=operator.lang))
    log.info(
        "Supporter grant target prompt re-sent",
        user_id=operator.db_id,
        stage="target",
        outcome="reprompted",
        reason="unsupported_message_type",
    )
    return ConversationGrantState.AWAITING_TARGET
