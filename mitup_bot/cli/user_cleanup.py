from sqlmodel import and_, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

INACTIVE_USERS_SELECT_STATEMENT: SelectOfScalar[int] | SelectOfScalar[None] = select(User.id).where(
    and_(User.status == UserStatus.LEFT, User.tg_user_id != -1)
)
"""Selects IDs of LEFT users who are not invited (outside) users.

JOINED_ONLY users are intentionally excluded — they are cleaned up exclusively
by `inactive_meetings` once none of their meetings remain active.
Invited users (tg_user_id == -1) are handled by `inactive_meetings` too.
"""


@db.with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient) -> None:
    user_ids = set((await session.exec(INACTIVE_USERS_SELECT_STATEMENT)).all())

    await session.exec(delete(User).where(User.id.in_(user_ids)))  # ty: ignore[unresolved-attribute]  # https://github.com/astral-sh/ty/issues/2839
    metrics.emit(MetricKey.INACTIVE_USERS_DELETED, len(user_ids), MetricUnit.COUNT)
