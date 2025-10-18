import datetime as dt
import logging

from aws_embedded_metrics.unit import Unit
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Session, and_, func, literal, null, or_, select, true
from sqlmodel.sql.expression import SelectOfScalar
from telegram.ext import ExtBot

from mitup_bot import api, db
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger

# The amount of time a meeting stays active after it has been created when there is no datetime set
INTERVAL_TO_DEACTIVATE = "1 year"

# Query to get all meetings to be deactivated
#   - The meeting is currently active
#   - The meeting has a datetime set
#   - The current time is past meeting.datetime + timeout from the owner's settings
#
# If the meeting does not ahve a datetime set, the meeting is deactivated INTERVAL_TO_DEACTIVATE from the creation date.
MEETINGS_TO_DEACTIVATE_STATEMENT: SelectOfScalar[Meetup] = (
    select(Meetup)
    .join(User)
    .join(Settings)
    .where(
        and_(
            Meetup.active == true(),
            or_(
                and_(
                    Meetup.datetime == null(),
                    Meetup.created_time + func.cast(literal(INTERVAL_TO_DEACTIVATE), INTERVAL) < func.now(),
                ),
                and_(
                    Meetup.datetime != null(),
                    func.now() > Meetup.datetime + func.cast(func.concat(Settings.timeout, " minutes"), INTERVAL),
                ),
            ),
        )
    )
)


@db.with_async_session
async def run(session: Session, bot: ExtBot, metrics: MitupMetricsLogger) -> None:
    """Mark meetings as inactive when they've been finished for longer than the configured timeout"""
    meetings = session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT).all()
    deactivated = 0
    failed = 0

    metrics.put_metric(MetricKey.MEETINGS_TO_DEACTIVATE.value, len(meetings), unit=Unit.COUNT.value)
    failed_details: list[str] = []

    for meeting in meetings:
        try:
            meeting.active = False
            meeting.expiration_time = dt.datetime.now(dt.UTC)

            # Update all messages using the existing API method
            await api.update_meeting_messages(
                session=session,
                context_or_bot=bot,
                meeting=meeting,
                has_finished=True,
            )

            deactivated += 1
        except Exception:
            failed += 1
            logging.exception(f"Failed to deactivate meeting (meeting: {meeting.id}, owner: {meeting.owner_id})")
            failed_details.append(f"Failed to deactivate meeting (meeting: {meeting.id}, owner: {meeting.owner_id})")

    if failed_details:
        metrics.set_property("failed_details", failed_details)
    metrics.put_metric(MetricKey.MEETINGS_DEACTIVATED.value, deactivated, unit=Unit.COUNT.value)
    metrics.put_metric(MetricKey.MEETINGS_DEACTIVATION_FAILED.value, failed, unit=Unit.COUNT.value)
