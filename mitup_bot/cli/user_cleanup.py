import structlog
from sqlmodel import and_, col, delete, select
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView

log = structlog.get_logger(__name__)

INACTIVE_USERS_SELECT_STATEMENT: SelectOfScalar[int] | SelectOfScalar[None] = select(User.id).where(
    and_(User.status == UserStatus.LEFT, User.tg_user_id != -1)
)
"""Selects IDs of LEFT users who are not invited (outside) users.

JOINED_ONLY users are intentionally excluded — they are cleaned up exclusively
by `inactive_meetings` once none of their meetings remain active.
Invited users (tg_user_id == -1) are handled by `inactive_meetings` too.
"""

DELETION_REQUESTED_USERS_SELECT_STATEMENT: SelectOfScalar[User] = select(User).where(
    User.status == UserStatus.DELETION_REQUESTED
)
"""Selects users who asked for their data to be erased.

Full rows, not just IDs: their farewell message needs the chat id and language,
which must be captured before the rows are gone.
"""


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Purge LEFT users silently and DELETION_REQUESTED users with a farewell message.

    The write lifecycle snapshots each farewell (chat id, text rendered in the user's
    language) at enqueue time, inside the transaction, and sends only after the deletion
    committed: a deletion is never announced before it happened, and a failed send —
    the user blocked the bot, a Telegram hiccup — leaves the deletion standing.
    """
    async with db.begin_write(api) as session:
        inactive_user_ids = set((await session.exec(INACTIVE_USERS_SELECT_STATEMENT)).all())
        marked_users = (await session.exec(DELETION_REQUESTED_USERS_SELECT_STATEMENT)).all()

        for user in marked_users:
            farewell = MitupView(description=PrivacyMessages.DELETION_COMPLETE.get(lang=user.lang), keyboard=[])
            await api.send_message_to_user(user, farewell)

        user_ids = inactive_user_ids | {user.db_id for user in marked_users}
        await session.exec(delete(User).where(col(User.id).in_(user_ids)))

    metrics.emit(MetricKey.INACTIVE_USERS_DELETED, len(inactive_user_ids), MetricUnit.COUNT)
    # Privacy purges are far too sparse for a useful CloudWatch series; the searchable log
    # carries the same information.
    log.info("Deletion-requested users purged", count=len(marked_users))
