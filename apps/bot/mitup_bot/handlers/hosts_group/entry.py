import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import hosts_group
from mitup_bot.db import with_session
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.supporter import is_supporter

from ..registry import HandlersRegistry
from .enums import HostsGroupHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_chat_join_request(handler_id=HostsGroupHandlerId.JOIN_REQUEST)
@with_session
async def hosts_group_join_request_handler(session: AsyncSession, update: Update, context: TMitupContext):
    # Approve an active host, decline everyone else (an unknown Telegram user included). Inert when
    # the feature is unconfigured, and only acts on join requests for the hosts-only group itself.
    chat_id = hosts_group.chat_id()
    if chat_id is None:
        return

    join_request = update.chat_join_request
    if join_request is None or join_request.chat.id != chat_id:
        return

    tg_user_id = join_request.from_user.id
    user = await User.by_tg_user_id(session, tg_user_id, load_collections=False)
    if user is not None and is_supporter(user.supporter_level):
        await context.api.approve_chat_join_request(chat_id, tg_user_id)
        log.info("Approved hosts-only group join request", chat_id=chat_id, tg_user_id=tg_user_id)
        return

    await context.api.decline_chat_join_request(chat_id, tg_user_id)
    log.info("Declined hosts-only group join request", chat_id=chat_id, tg_user_id=tg_user_id)
