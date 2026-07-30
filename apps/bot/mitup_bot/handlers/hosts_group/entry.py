import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import ChatJoinRequest, Update

from mitup_bot import hosts_group
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.supporter import is_supporter

from ..registry import HandlersRegistry
from .enums import HostsGroupHandlerId

log = structlog.get_logger(__name__)

# The one gate decision, under one event name: `outcome` says what the gate did, `reason` says why
# it decided that, and `applied` says whether Telegram carried it out. One `stats count() by
# outcome, reason` therefore separates a stranger from a registered non-patron from a lapsed host.
GATE_EVENT = "Hosts-only group join request gated"

# The requests this gate does not act on, under one event name and a reason apiece — an inert gate
# and a gate seeing no traffic are otherwise identical in the log.
IGNORED_EVENT = "Ignored chat join request"


@HandlersRegistry.register_chat_join_request(handler_id=HostsGroupHandlerId.JOIN_REQUEST)
@with_session
async def hosts_group_join_request_handler(session: AsyncSession, update: Update, context: TMitupContext):
    # Approve an active host, decline everyone else (an unknown Telegram user included). Inert when
    # the feature is unconfigured, and only acts on join requests for the hosts-only group itself.
    chat_id = hosts_group.chat_id()
    if chat_id is None:
        log.info(IGNORED_EVENT, stage="gate", outcome="ignored", reason="hosts_group_not_configured")
        return

    join_request = update.chat_join_request
    if join_request is None:
        log.warning(
            IGNORED_EVENT,
            stage="gate",
            outcome="ignored",
            reason="no_join_request_payload",
            hosts_group_chat_id=chat_id,
        )
        return
    if join_request.chat.id != chat_id:
        # The ambient `chat_id` bind names the chat the request came from, so the configured id is
        # what the line has to add: a group recreated behind a stale config reads as a dead gate.
        log.info(IGNORED_EVENT, stage="gate", outcome="ignored", reason="other_chat", hosts_group_chat_id=chat_id)
        return

    tg_user_id = join_request.from_user.id
    user = await User.by_tg_user_id(session, tg_user_id, load_collections=False)
    if user is not None and is_supporter(user.supporter_level):
        applied = await context.api.approve_chat_join_request(chat_id, tg_user_id)
        log_gate_decision(join_request, user, granted=True, applied=applied, reason="active_supporter")
        return

    applied = await context.api.decline_chat_join_request(chat_id, tg_user_id)
    reason = "not_a_supporter" if user is not None else "unknown_telegram_user"
    log_gate_decision(join_request, user, granted=False, applied=applied, reason=reason)


def log_gate_decision(join_request: ChatJoinRequest, user: User | None, *, granted: bool, applied: bool, reason: str):
    """Record the gate decision together with the evidence it was taken on.

    A refused Telegram call is a warning on the same event rather than a name of its own: the
    decision and its reason are unchanged, only the delivery failed, so `outcome` carries the
    failure and `reason` keeps naming why the gate decided what it decided.
    """
    decided, failed = ("approved", "approve_failed") if granted else ("declined", "decline_failed")
    invite_link = join_request.invite_link
    emit = log.info if applied else log.warning
    emit(
        GATE_EVENT,
        user_id=user.db_id if user is not None else None,
        supporter_level=user.supporter_level.value if user is not None else None,
        user_status=user.status.value if user is not None else None,
        stage="gate",
        outcome=decided if applied else failed,
        reason=reason,
        applied=applied,
        invite_link_name=invite_link.name if invite_link is not None else None,
    )
