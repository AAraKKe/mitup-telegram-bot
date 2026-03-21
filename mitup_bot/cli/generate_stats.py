from sqlmodel import Integer, Session, cast, distinct, func, null, select

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.db import with_session
from mitup_bot.models import Meetup, Message, Settings, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

EMPTY_USERS_TABLE_ERROR = "EmptyUsersTable"
EMPTY_MEETINGS_TABLE_ERROR = "EmptyMeetingsTable"


def users_stats(session: Session, metrics: MetricsClient):
    active_users = func.sum(cast(User.is_active, Integer))
    total_users = func.count()
    invited_users = func.sum(cast(User.tg_user_id == -1, Integer))
    result = session.exec(select(active_users, total_users, invited_users)).first()

    if result is None:
        metrics.emit(MetricKey.FAULT.with_prefix(EMPTY_USERS_TABLE_ERROR), 1, MetricUnit.COUNT)
        return

    metrics.emit(MetricKey.ACTIVE_USERS, result[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.INACTIVE_USERS, result[1] - result[0], MetricUnit.COUNT)
    metrics.emit(MetricKey.INVITED_USERS, result[2], MetricUnit.COUNT)
    metrics.emit(MetricKey.FAULT.with_prefix(EMPTY_USERS_TABLE_ERROR), 0, MetricUnit.COUNT)

    # Get user language stats
    user_languages = session.exec(select(Settings.language, func.count()).group_by(Settings.language)).all()

    for language, count in user_languages:
        metrics.emit(MetricKey.ACTIVE_USERS.with_prefix(language), count, MetricUnit.COUNT)


def meetings_stats(session: Session, metrics: MetricsClient):
    active_meetings = func.sum(cast(Meetup.active, Integer))
    total_meetings = func.count()
    incognito_meetings = func.sum(cast(Meetup.incognito, Integer))
    public_meetings = func.sum(cast(Meetup.public, Integer))
    meetings_with_invitation = func.sum(cast(Meetup.allow_invitation, Integer))
    meetings_with_datetime = func.sum(cast(Meetup.datetime != null(), Integer))

    result: tuple[int, int, int, int, int, int] = session.exec(
        select(  # type: ignore no overload for "exec" matches argument types
            active_meetings,
            total_meetings,
            incognito_meetings,
            public_meetings,
            meetings_with_invitation,
            meetings_with_datetime,
        )
    ).first()

    if result[1] is None:
        # It is more common that there are no meetings than there are no users, we still
        # consider this pretty rare and should emit a fault metric in case this happens.
        metrics.emit(MetricKey.FAULT.with_prefix(EMPTY_MEETINGS_TABLE_ERROR), 1, MetricUnit.COUNT)
        return

    metrics.emit(MetricKey.ACTIVE_MEETINGS, result[0] or 0, MetricUnit.COUNT)
    metrics.emit(MetricKey.INACTIVE_MEETINGS, (result[1] or 0) - (result[0] or 0), MetricUnit.COUNT)
    metrics.emit(MetricKey.INCOGNITO_MEETINGS, result[2] or 0, MetricUnit.COUNT)
    metrics.emit(MetricKey.PUBLIC_MEETINGS, result[3] or 0, MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_WITH_INVITATION, result[4] or 0, MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_WITH_DATETIME, result[5] or 0, MetricUnit.COUNT)
    metrics.emit(MetricKey.FAULT.with_prefix(EMPTY_MEETINGS_TABLE_ERROR), 0, MetricUnit.COUNT)

    # Get number of shared meetings through inline messages
    shared_meetings = func.count(distinct(Message.meetup_id))
    count_result = session.exec(select(shared_meetings).where(Message.inline_message_id != null())).first()

    if count_result is None:
        # It can happen that there are no meetings shared
        count_result = 0

    metrics.emit(MetricKey.SHARED_MEETINGS, count_result, MetricUnit.COUNT)


@with_session
def run(session: Session, api: TelegramApiWrapper, metrics: MetricsClient):
    users_stats(session, metrics)
    meetings_stats(session, metrics)
