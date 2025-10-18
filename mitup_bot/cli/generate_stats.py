from aws_embedded_metrics.unit import Unit
from sqlmodel import Integer, Session, cast, distinct, func, null, select
from telegram.ext import ExtBot

from mitup_bot.db import with_session
from mitup_bot.models import Meetup, Message, Settings, User
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger

EMPTY_USERS_TABLE_ERROR = "EmptyUsersTable"
EMPTY_MEETINGS_TABLE_ERROR = "EmptyMeetingsTable"


def users_stats(session: Session, metrics: MitupMetricsLogger):
    active_users = func.sum(cast(User.is_active, Integer))
    total_users = func.count()
    invited_users = func.sum(cast(User.tg_user_id == -1, Integer))
    result = session.exec(select(active_users, total_users, invited_users)).first()

    if result is None:
        metrics.put_metric(MetricKey.FAULT.with_prefix(EMPTY_USERS_TABLE_ERROR), 1, Unit.COUNT.value)
        return

    metrics.put_metric(MetricKey.ACTIVE_USERS.value, result[0], Unit.COUNT.value)
    metrics.put_metric(MetricKey.INACTIVE_USERS.value, result[1] - result[0], Unit.COUNT.value)
    metrics.put_metric(MetricKey.INVITED_USERS.value, result[2], Unit.COUNT.value)
    metrics.put_metric(MetricKey.FAULT.with_prefix(EMPTY_USERS_TABLE_ERROR), 0, Unit.COUNT.value)

    # Get user language stats
    user_languages = session.exec(select(Settings.language, func.count()).group_by(Settings.language)).all()

    for language, count in user_languages:
        metrics.put_metric(MetricKey.ACTIVE_USERS.with_prefix(language), count, Unit.COUNT.value)


def meetings_stats(session: Session, metrics: MitupMetricsLogger):
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

    if result is None:
        # It is more common that there are no meetings than there are no users, we still
        # consider this pretty rare and should emit a fault metric in case this happens.
        metrics.put_metric(MetricKey.FAULT.with_prefix(EMPTY_MEETINGS_TABLE_ERROR), 1, Unit.COUNT.value)
        return

    metrics.put_metric(MetricKey.ACTIVE_MEETINGS.value, result[0], Unit.COUNT.value)
    metrics.put_metric(MetricKey.INACTIVE_MEETINGS.value, result[1] - result[0], Unit.COUNT.value)
    metrics.put_metric(MetricKey.INCOGNITO_MEETINGS.value, result[2], Unit.COUNT.value)
    metrics.put_metric(MetricKey.PUBLIC_MEETINGS.value, result[3], Unit.COUNT.value)
    metrics.put_metric(MetricKey.MEETINGS_WITH_INVITATION.value, result[4], Unit.COUNT.value)
    metrics.put_metric(MetricKey.MEETINGS_WITH_DATETIME.value, result[5], Unit.COUNT.value)
    metrics.put_metric(MetricKey.FAULT.with_prefix(EMPTY_MEETINGS_TABLE_ERROR), 0, Unit.COUNT.value)

    # Get number of shared meetings through inline messages
    shared_meetings = func.count(distinct(Message.meetup_id))
    count_result = session.exec(select(shared_meetings).where(Message.inline_message_id != null())).first()

    if count_result is None:
        # It can happen that there are no meetings shared
        count_result = 0

    metrics.put_metric(MetricKey.SHARED_MEETINGS.value, count_result, Unit.COUNT.value)


@with_session
def run(session: Session, _: ExtBot, metrics: MitupMetricsLogger):
    users_stats(session, metrics)
    meetings_stats(session, metrics)
