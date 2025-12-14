from sqlmodel import Session, delete, false, select

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger


@db.with_session
def run(session: Session, api: TelegramApiWrapper, metrics: MitupMetricsLogger) -> None:
    select_statement = select(User.id).where(User.is_active == false())
    user_ids = set(session.exec(select_statement).all())

    # Delete ianactive users, ignore typing because for some reason sqlalchmey does not recognize User as a valid type
    session.exec(delete(User).where(User.id.in_(user_ids)))  # type: ignore
    metrics.put_metric(MetricKey.INACTIVE_USERS_DELETED.value, len(user_ids))
