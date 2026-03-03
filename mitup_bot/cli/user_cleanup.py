from sqlmodel import Session, and_, delete, false, select
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger, Unit

INACTIVE_USERS_SELECT_STATEMENT: SelectOfScalar[int] | SelectOfScalar[None] = select(User.id).where(
    and_(User.is_active == false(), User.tg_user_id != -1)
)
"""Selects IDs of inactive users who are not invited (outside) users. Invited users have tg_user_id == -1."""


@db.with_session
def run(session: Session, api: TelegramApiWrapper, metrics: MitupMetricsLogger) -> None:
    user_ids = set(session.exec(INACTIVE_USERS_SELECT_STATEMENT).all())

    session.exec(delete(User).where(User.id.in_(user_ids)))  # ty: ignore[unresolved-attribute]  # https://github.com/astral-sh/ty/issues/2839
    metrics.put_metric(MetricKey.INACTIVE_USERS_DELETED.value, len(user_ids), unit=Unit.COUNT.value)
