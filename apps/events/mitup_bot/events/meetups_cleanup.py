import datetime as dt
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from typing import Any, cast

import structlog
from sqlmodel import and_, col, delete, false, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

from .lifecycle_queries import loggable_windows, owner_tier_window_elapsed
from .telemetry import emit_per_supporter_level

log = structlog.get_logger(__name__)

# A residue row is expected to clear on the next daily run. Past this many days the phase is not
# retrying any more, it is stuck, and repeating the same warning every day has stopped being news.
STUCK_RESIDUE_DAYS = 7

# Both windows run from `expiration_time` (the deactivation stamp) and both depend on the owner's
# tier, so each statement joins the owner.
MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT: SelectOfScalar[Meetup] = (
    select(Meetup)
    .join(User)
    .where(
        and_(
            Meetup.expiration_notification_sent == false(),
            Meetup.expiration_time != null(),
            owner_tier_window_elapsed(col(Meetup.expiration_time), lambda policy: policy.deletion_warning_delay),
        )
    )
)

MEETUPS_TO_DELETE_STATEMENT: SelectOfScalar[Meetup] = (
    select(Meetup)
    .join(User)
    .where(
        and_(
            Meetup.expiration_notification_sent == true(),
            Meetup.expiration_time != null(),
            owner_tier_window_elapsed(col(Meetup.expiration_time), lambda policy: policy.inactive_retention),
        )
    )
)


class ResidueReason(StrEnum):
    """Why a nominated meetup did not get the notice its phase intended to deliver."""

    OWNER_UNREACHABLE = "owner_unreachable"
    OWNER_NOTIFICATION_FAILED = "owner_notification_failed"


class WarningDelivery(StrEnum):
    """Whether the owner of a warned meetup actually received the notice.

    Both dispositions flip `expiration_notification_sent`, so this is the only thing that keeps
    them apart once the flag is written.
    """

    DELIVERED = "delivered"
    UNREACHABLE = "unreachable"


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


def owner_policy(meetup: Meetup) -> LifecyclePolicy:
    """The lifecycle policy this meeting runs on, read off its owner's current tier."""
    return LifecyclePolicy.get(meetup.owner.supporter_level)


def window_days(meetup: Meetup, duration_of: Callable[[LifecyclePolicy], dt.timedelta]) -> int:
    """The window, in whole days, this phase applied to this meetup, read off its owner's tier.

    The same `LifecyclePolicy.get(level)` lookup the statement's SQL branches are generated from,
    resolved against the owner row the statement already joined and loaded.
    """
    return LifecyclePolicy.interval_days(duration_of(owner_policy(meetup)))


def owner_levels(meetups: Sequence[Meetup]) -> Counter[SupporterLevel]:
    """How many of `meetups` each supporter tier owns."""
    return Counter(meetup.owner.supporter_level for meetup in meetups)


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
    """Record a nominated meetup its phase left behind, escalating one that stopped moving.

    A send that raised defers the meetup to the next run, which re-nominates and re-warns it at
    the same level forever; past `STUCK_RESIDUE_DAYS` the retry is not working and the daily
    warning is no longer news, so the row gets an error of its own instead. An unreachable owner
    is not a retry — the meetup is marked or deleted in the same run — so it never escalates.
    """
    overdue = days_overdue(meetup, age)
    stuck = reason is ResidueReason.OWNER_NOTIFICATION_FAILED and overdue is not None and overdue > STUCK_RESIDUE_DAYS
    if stuck:
        log.error(
            "Meeting deletion stuck",
            meeting_id=meetup.id,
            tg_user_id=meetup.owner.tg_user_id,
            supporter_level=meetup.owner.supporter_level.value,
            days_overdue=overdue,
            stuck_after_days=STUCK_RESIDUE_DAYS,
            reason="owner_notification_failed_repeatedly",
        )
        return

    log.warning(
        event,
        meeting_id=meetup.id,
        tg_user_id=meetup.owner.tg_user_id,
        supporter_level=meetup.owner.supporter_level.value,
        days_overdue=overdue,
        reason=reason.value,
    )


def failed_meeting_properties(meetups: Sequence[Meetup]) -> dict[str, Any] | None:
    """EMF properties naming the meetings a run left behind, or None when it left none."""
    return {"failed_meeting_ids": [meetup.id for meetup in meetups]} if meetups else None


def deletion_warning_view(meetup: Meetup) -> MitupView:
    return MitupView(
        description=NotificationMessages.DELETION_WARNING.get(
            lang=meetup.lang,
            meeting_title=rich_title(meetup),
            # The message promises a deadline, so the lead has to be the owner's own: the free
            # policy's value is only right for a free owner.
            days_until_deletion=window_days(meetup, lambda policy: policy.deletion_warning_lead),
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


def record_warning(meetup: Meetup, delivery: WarningDelivery):
    """The mutation record for `expiration_notification_sent = True`.

    That flag is the precondition for the later permanent deletion and is written for a delivered
    warning and an undeliverable one alike, so this line is the only place the two stay
    distinguishable once the run is over.
    """
    log.info(
        "Meetup deletion warning recorded",
        meeting_id=meetup.id,
        tg_user_id=meetup.owner.tg_user_id,
        supporter_level=meetup.owner.supporter_level.value,
        window_days=window_days(meetup, lambda policy: policy.deletion_warning_delay),
        days_until_deletion=window_days(meetup, lambda policy: policy.deletion_warning_lead),
        expiration_time=meetup.expiration_time,
        delivery=delivery.value,
        reason="warning_window_elapsed",
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
            owner_policy(meetup).deletion_warning_delay,
        )
    for meetup in outcome.failed:
        log_residue(
            "Expiration warning failed",
            meetup,
            ResidueReason.OWNER_NOTIFICATION_FAILED,
            owner_policy(meetup).deletion_warning_delay,
        )

    for meetup in outcome.delivered:
        record_warning(meetup, WarningDelivery.DELIVERED)
    for meetup in outcome.unreachable:
        record_warning(meetup, WarningDelivery.UNREACHABLE)

    for meetup in outcome.delivered + outcome.unreachable:
        meetup.expiration_notification_sent = True

    log.info(
        "Deletion warning sweep complete",
        nominated=len(meetups),
        delivered=len(outcome.delivered),
        unreachable=len(outcome.unreachable),
        failed=len(outcome.failed),
        windows=loggable_windows(lambda policy: policy.deletion_warning_delay),
        reason="warning_window_elapsed",
    )

    metrics.emit(MetricKey.MEETUPS_ABOUT_TO_BE_DELETED, len(meetups), MetricUnit.COUNT)
    metrics.emit(
        MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        len(outcome.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(outcome.failed),
    )


async def delete_meetups(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    """Permanently delete every meeting that stayed expired past its owner's `inactive_retention`.

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
            owner_policy(meetup).inactive_retention,
        )
    for meetup in outcome.failed:
        log_residue(
            "Meeting deletion deferred",
            meetup,
            ResidueReason.OWNER_NOTIFICATION_FAILED,
            owner_policy(meetup).inactive_retention,
        )

    deletable = outcome.delivered + outcome.unreachable
    meeting_ids = [cast(int, meetup.id) for meetup in deletable]
    # Invited users exist only in the context of the meeting they were invited to.
    outside_user_ids = [
        cast(int, link.user.id) for meetup in deletable for link in meetup.joined_links if link.user.tg_user_id == -1
    ]

    # Named before the DELETE: after it nothing can reconstruct which meetings a run took, and the
    # invitee rows have no other trace at all.
    log.info(
        "Meetups purged",
        count=len(deletable),
        meeting_ids=meeting_ids,
        supporter_levels={level.value: count for level, count in owner_levels(deletable).items()},
        windows=loggable_windows(lambda policy: policy.inactive_retention),
        unnotified=len(outcome.unreachable),
        reason="retention_elapsed",
    )
    log.info(
        "Invitee users purged",
        count=len(outside_user_ids),
        user_ids=outside_user_ids,
        reason="cascade_of_purged_meetups",
    )

    await session.exec(delete(Meetup).where(col(Meetup.id).in_(meeting_ids)))
    await session.exec(delete(User).where(col(User.id).in_(outside_user_ids)))

    log.info(
        "Meetup purge sweep complete",
        nominated=len(meetups),
        delivered=len(outcome.delivered),
        unreachable=len(outcome.unreachable),
        failed=len(outcome.failed),
        purged=len(deletable),
        invitee_users_purged=len(outside_user_ids),
    )

    emit_per_supporter_level(metrics, MetricKey.MEETUPS_DELETED, owner_levels(deletable))
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
