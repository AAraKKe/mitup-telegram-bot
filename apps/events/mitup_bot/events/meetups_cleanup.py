import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import Any, cast

import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, delete, false, func, literal, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

log = structlog.get_logger(__name__)

"""Meeting permanent expiration age. After having expired, if the meeting is still inactive, it will be deleted."""
PERMANENT_EXPIRATION_AGE = dt.timedelta(days=180)
"""Age after expiration at which the owner is notified that the meeting will be permanently deleted."""
PERMANENT_EXPIRATION_NOTIFICATION_AGE = PERMANENT_EXPIRATION_AGE - dt.timedelta(days=7)


def sql_interval(age: dt.timedelta) -> str:
    """Render *age* as a Postgres interval literal."""
    return f"{age.days} days"


MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.expiration_notification_sent == false(),
        Meetup.expiration_time != null(),
        Meetup.expiration_time + func.cast(literal(sql_interval(PERMANENT_EXPIRATION_NOTIFICATION_AGE)), INTERVAL)
        < func.now(),
    )
)

MEETUPS_TO_DELETE_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.expiration_notification_sent == true(),
        Meetup.expiration_time != null(),
        Meetup.expiration_time + func.cast(literal(sql_interval(PERMANENT_EXPIRATION_AGE)), INTERVAL) < func.now(),
    )
)


class ResidueReason(StrEnum):
    """Why a nominated meetup did not get the notice its phase intended to deliver."""

    OWNER_UNREACHABLE = "owner_unreachable"
    OWNER_NOTIFICATION_FAILED = "owner_notification_failed"


@dataclass
class SendOutcome:
    """The meetups of one cleanup fan-out, bucketed by how the notice to their owner resolved."""

    delivered: list[Meetup] = field(default_factory=list)
    unreachable: list[Meetup] = field(default_factory=list)
    failed: list[Meetup] = field(default_factory=list)


def bucket_meetup(_user: User, *, bucket: list[Meetup], meetup: Meetup):
    bucket.append(meetup)


def bucket_failed_meetup(_user: User, _error: Exception, *, bucket: list[Meetup], meetup: Meetup):
    bucket.append(meetup)


async def notify_owners(
    api: TelegramApiWrapper, meetups: Sequence[Meetup], build_view: Callable[[Meetup], MitupView]
) -> SendOutcome:
    """Send `build_view(meetup)` to each meetup's owner and report which meetups reached them."""
    outcome = SendOutcome()
    if not meetups:
        return outcome

    await api.send_messages_to_users(
        users=[meetup.owner for meetup in meetups],
        views=[build_view(meetup) for meetup in meetups],
        on_success=[partial(bucket_meetup, bucket=outcome.delivered, meetup=meetup) for meetup in meetups],
        on_unreachable=[partial(bucket_meetup, bucket=outcome.unreachable, meetup=meetup) for meetup in meetups],
        on_error=[partial(bucket_failed_meetup, bucket=outcome.failed, meetup=meetup) for meetup in meetups],
    )
    return outcome


def days_overdue(meetup: Meetup, age: dt.timedelta) -> int | None:
    """Whole days the meetup has been eligible for its phase, or None when it never expired.

    The expiration column is naive UTC, so a stored value is read back without a timezone.
    """
    if meetup.expiration_time is None:
        return None
    expiration = meetup.expiration_time
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - expiration - age).days


def log_residue(event: str, meetup: Meetup, reason: ResidueReason, age: dt.timedelta):
    log.warning(event, reason=reason.value, meeting_id=meetup.id, days_overdue=days_overdue(meetup, age))


def failed_meeting_properties(meetups: Sequence[Meetup]) -> dict[str, Any] | None:
    """EMF properties naming the meetings a run left behind, or None when it left none."""
    return {"failed_meeting_ids": [meetup.id for meetup in meetups]} if meetups else None


def deletion_warning_view(meetup: Meetup) -> MitupView:
    return MitupView(
        description=NotificationMessages.DELETION_WARNING.get(
            lang=meetup.lang,
            meeting_title=rich_title(meetup),
            days_until_deletion=(PERMANENT_EXPIRATION_AGE - PERMANENT_EXPIRATION_NOTIFICATION_AGE).days,
            past_meetings_button=ButtonMessages.PAST_MEETINGS.get(lang=meetup.user_language),
            reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.get(lang=meetup.user_language),
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get_text(lang=meetup.user_language),
                    callback_data=cb.REACTIVATE_MEETING.with_id(cast(int, meetup.id)),
                ),
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meetup.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ]
        ],
    )


def deletion_notice_view(meetup: Meetup) -> MitupView:
    return MitupView(
        description=NotificationMessages.DELETED.get(lang=meetup.lang, meeting_title=rich_title(meetup)),
        keyboard=[],
    )


async def notify_meetups_about_to_be_deleted(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    """Warn every owner whose meeting is a week away from permanent deletion.

    An owner who has blocked the bot can never receive the warning, so their meeting is marked
    as warned anyway and moves on to the deletion pool instead of being re-warned on every run.
    A send that raised leaves the meeting in the pool for the next run to retry.
    """
    meetups = (await session.exec(MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT)).all()
    outcome = await notify_owners(api, meetups, deletion_warning_view)

    for meetup in outcome.unreachable:
        log_residue(
            "Expiration warning undelivered",
            meetup,
            ResidueReason.OWNER_UNREACHABLE,
            PERMANENT_EXPIRATION_NOTIFICATION_AGE,
        )
    for meetup in outcome.failed:
        log_residue(
            "Expiration warning failed",
            meetup,
            ResidueReason.OWNER_NOTIFICATION_FAILED,
            PERMANENT_EXPIRATION_NOTIFICATION_AGE,
        )

    for meetup in outcome.delivered + outcome.unreachable:
        meetup.expiration_notification_sent = True

    metrics.emit(MetricKey.MEETUPS_ABOUT_TO_BE_DELETED, len(meetups), MetricUnit.COUNT)
    metrics.emit(
        MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        len(outcome.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(outcome.failed),
    )


async def delete_meetups(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    """Permanently delete every meeting that stayed expired past `PERMANENT_EXPIRATION_AGE`.

    Delivering the notice is not a precondition for the deletion: an owner who has blocked the
    bot has opted out of the notice, and keeping their expired meetings alive to keep retrying
    it would retain the data forever. Only a send that raised — a transient Telegram failure —
    defers the deletion to the next run.
    """
    meetups = (await session.exec(MEETUPS_TO_DELETE_STATEMENT)).all()
    outcome = await notify_owners(api, meetups, deletion_notice_view)

    for meetup in outcome.unreachable:
        log_residue(
            "Meeting deleted without notifying its owner",
            meetup,
            ResidueReason.OWNER_UNREACHABLE,
            PERMANENT_EXPIRATION_AGE,
        )
    for meetup in outcome.failed:
        log_residue(
            "Meeting deletion deferred", meetup, ResidueReason.OWNER_NOTIFICATION_FAILED, PERMANENT_EXPIRATION_AGE
        )

    deletable = outcome.delivered + outcome.unreachable
    meeting_ids = [cast(int, meetup.id) for meetup in deletable]
    # Invited users exist only in the context of the meeting they were invited to.
    outside_user_ids = [
        cast(int, link.user.id) for meetup in deletable for link in meetup.joined_links if link.user.tg_user_id == -1
    ]

    await session.exec(delete(Meetup).where(col(Meetup.id).in_(meeting_ids)))
    await session.exec(delete(User).where(col(User.id).in_(outside_user_ids)))

    metrics.emit(MetricKey.MEETUPS_DELETED, len(deletable), MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETUPS_DELETED_UNNOTIFIED, len(outcome.unreachable), MetricUnit.COUNT)
    metrics.emit(
        MetricKey.MEETINGS_DELETION_FAILED,
        len(outcome.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(outcome.failed),
    )


@db.with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    await notify_meetups_about_to_be_deleted(session, api, metrics)
    await delete_meetups(session, api, metrics)
