import datetime as dt
from collections.abc import Callable
from enum import StrEnum
from functools import partial

import structlog
from sqlmodel import and_, col, false, func, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.datetimes import as_utc
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.views import meeting as meeting_views

from .deletion_sweep import (
    NoticeOutcome,
    failed_meeting_properties,
    log_residues,
    nominate_owners,
    notify_owners,
    owner_count,
    raise_for_failed_chunks,
    sweep_chunks,
)
from .lifecycle_queries import loggable_windows, owner_tier_window_elapsed

log = structlog.get_logger(__name__)

# The window depends on the owner's tier, so the statement joins the owner. It runs from
# `expiration_time`, the deactivation stamp.
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

# The backlog gauge counts every warned meeting still to be deleted, not only what is due today.
MEETINGS_AWAITING_DELETION_COUNT_STATEMENT: SelectOfScalar[int] = (
    select(func.count()).select_from(Meetup).where(Meetup.expiration_notification_sent == true())
)


class WarningDelivery(StrEnum):
    """How the warning owed to the owner of a warned meetup resolved.

    Every disposition records the warning, so this is the only thing that keeps them apart once the
    stamp is written.
    """

    DELIVERED = "delivered"
    UNREACHABLE = "unreachable"
    OPTED_OUT = "opted_out"


def window_days(meetup: Meetup, duration_of: Callable[[LifecyclePolicy], dt.timedelta]) -> int:
    """The window, in whole days, this sweep applied to this meetup, read off its owner's tier.

    The same `LifecyclePolicy.get(level)` lookup the statement's SQL branches are generated from,
    resolved against the owner row the statement already joined and loaded.
    """
    return LifecyclePolicy.interval_days(duration_of(meetup.lifecycle_policy))


def warning_due_time(meetup: Meetup) -> dt.datetime | None:
    """When the warning became due, or None when the meetup never expired."""
    expiration = meetup.expiration_time
    return None if expiration is None else as_utc(expiration) + meetup.lifecycle_policy.deletion_warning_delay


def record_warning(meetup: Meetup, delivery: WarningDelivery):
    """The mutation record for `record_deletion_warning`.

    That stamp is the precondition for the later permanent deletion and is written whichever way
    the warning resolved, so this line is the only place the dispositions stay distinguishable once
    the run is over.
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


@db.with_session
async def warn_owner_chunk(session: AsyncSession, api: TelegramApiWrapper, owner_ids: list[int]) -> NoticeOutcome:
    """Warn one chunk of owners and record the warning on every meeting their digest covered.

    The digests go out before the marks are written and both commit together, so a failure can only
    understate what was sent: the next daily run re-nominates whatever this chunk left unmarked.
    """
    meetups = (await session.exec(MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT.where(col(User.id).in_(owner_ids)))).all()
    outcome = await notify_owners(
        api, meetups, lambda settings: settings.deletion_warning, meeting_views.deletion_warning_view
    )

    log_residues(
        outcome,
        warning_due_time,
        unreachable_event="Expiration warning undelivered",
        failed_event="Expiration warning failed",
    )

    for meetup in outcome.delivered:
        record_warning(meetup, WarningDelivery.DELIVERED)
    for meetup in outcome.unreachable:
        record_warning(meetup, WarningDelivery.UNREACHABLE)
    for meetup in outcome.opted_out:
        record_warning(meetup, WarningDelivery.OPTED_OUT)

    for meetup in outcome.settled:
        meetup.record_deletion_warning()

    return outcome


@db.with_session
async def emit_backlog_gauge(session: AsyncSession, metrics: MetricsClient):
    """Report how many warned meetings are still waiting for their deletion.

    Counted before this run warns anything, so consecutive days measure the same point in the cycle.
    """
    awaiting_deletion = (await session.exec(MEETINGS_AWAITING_DELETION_COUNT_STATEMENT)).one()
    metrics.emit(MetricKey.MEETINGS_AWAITING_DELETION, awaiting_deletion, MetricUnit.COUNT)
    log.info("Deletion backlog measured", awaiting_deletion=awaiting_deletion)


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Warn every owner whose meetings are a week away from permanent deletion, one digest each.

    An owner who has blocked the bot can never receive the warning, and one who turned the warning
    off does not want it, so either way the meeting is marked as warned and moves on to the
    deletion pool instead of being re-warned on every run. A send that raised leaves every meeting
    of that owner's digest in the pool for the next run to retry.
    """
    await emit_backlog_gauge(metrics)
    nomination = await nominate_owners(MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT)
    totals = await sweep_chunks(nomination, partial(warn_owner_chunk, api))

    log.info(
        "Deletion warning sweep complete",
        nominated=nomination.meetup_count,
        delivered=len(totals.delivered),
        unreachable=len(totals.unreachable),
        opted_out=len(totals.opted_out),
        failed=len(totals.failed),
        owners_messaged=owner_count(totals.delivered),
        owners_unreachable=owner_count(totals.unreachable),
        owners_opted_out=owner_count(totals.opted_out),
        owners_failed=owner_count(totals.failed),
        chunks=len(nomination.chunks),
        failed_chunks=totals.failed_chunks,
        windows=loggable_windows(lambda policy: policy.deletion_warning_delay),
        reason="warning_window_elapsed",
    )

    # A failed send never raises, so a sweep whose chunks all committed closes on Fault=0: this
    # counter is the only alarmable trace of a warning that never reached its owner.
    metrics.emit(
        MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        len(totals.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(totals.failed),
    )
    raise_for_failed_chunks(totals.failed_chunks)
