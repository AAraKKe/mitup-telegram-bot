import datetime as dt
import logging
import traceback
from typing import cast

from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import Session, and_, delete, func, literal, null, or_, select, true
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import Meetup, Message, Settings, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

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
                    func.now()
                    > Meetup.datetime
                    + func.cast(
                        func.concat(func.coalesce(Meetup.duration_minutes, Settings.timeout), " minutes"),
                        INTERVAL,
                    ),
                ),
            ),
        )
    )
)


@db.with_async_session
async def run(session: Session, api: TelegramApiWrapper, metrics: MetricsClient) -> None:
    """Mark meetings as inactive when they've been finished for longer than the configured timeout"""
    meetings = session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT).all()
    deactivated = 0
    failed = 0
    invited_users_ids: list[int] = []

    metrics.emit(MetricKey.MEETINGS_TO_DEACTIVATE, len(meetings), MetricUnit.COUNT)
    failed_details: list[str] = []

    for meeting in meetings:
        try:
            # Update all messages using the existing API method
            await api.update_meeting_messages(
                session=session,
                meeting=meeting,
                has_finished=True,
            )

            meeting.active = False
            meeting.expiration_time = dt.datetime.now(dt.UTC)

            # Keep track of all invited users of the deactivated meetings to be deleted at the end
            invited_users_ids.extend(
                [cast(int, link.user_id) for link in meeting.joined_links if link.user.tg_user_id == -1]
            )

            deactivated += 1

            # Delete all users that were added to the meeting that were invited.
            # These users exist only in the context of the current meeting.
            session.exec(delete(User).where(User.id.in_(invited_users_ids)))  # type: ignore

            # Same with messages, any messagea attached to this meeting is left untracked as the
            # meeting is now considered over
            session.exec(delete(Message).where(Message.meetup_id == meeting.id))  # type: ignore
        except Exception as e:
            failed += 1
            logging.exception(f"Failed to deactivate meeting (meeting: {meeting.id}, owner: {meeting.owner_id})")
            failed_details.append(
                f"Failed to deactivate meeting (meeting: {meeting.id}, owner: {meeting.owner_id}). Error: {e}.\n"
                f"Stack trace: {traceback.format_exc()}"
            )

    metrics.emit(MetricKey.MEETINGS_DEACTIVATED, deactivated, MetricUnit.COUNT)
    metrics.emit(
        MetricKey.MEETINGS_DEACTIVATION_FAILED,
        failed,
        MetricUnit.COUNT,
        properties={"failed_details": failed_details} if failed_details else None,
    )

    if failed:
        raise RuntimeError(
            f"Failed to deactivate {failed} meetings. Check individual failed_details for more information."
        )
