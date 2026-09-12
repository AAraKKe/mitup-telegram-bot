import datetime as dt
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
from mitup_bot.datetimes import as_utc
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import title_content

from .lifecycle_queries import loggable_windows, owner_tier_window_elapsed
from .telemetry import supporter_level_counts

log = structlog.get_logger(__name__)

# A residue row is expected to clear on the next daily run. Past this many days the phase is not
# retrying any more, it is stuck, and repeating the same warning every day has stopped being news.
STUCK_RESIDUE_DAYS = 7

# Every window depends on the owner's tier, so each statement joins the owner. The warning runs from
# `expiration_time` (the deactivation stamp); the deletion runs from it too but additionally waits out
# the lead from `warned_time`, so it can never land sooner than the warning promised, however late
# that warning was issued. A row whose `warned_time` is NULL satisfies neither comparison.
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
            owner_tier_window_elapsed(col(Meetup.warned_time), lambda policy: policy.deletion_warning_lead),
        )
    )
)


class ResidueReason(StrEnum):
    """Why a nominated meetup did not get the notice its phase intended to deliver."""

    OWNER_UNREACHABLE = "owner_unreachable"
    OWNER_NOTIFICATION_FAILED = "owner_notification_failed"


class WarningDelivery(StrEnum):
    """How the warning owed to the owner of a warned meetup resolved.

    Every disposition records the warning, so this is the only thing that keeps them apart once the
    stamp is written.
    """

    DELIVERED = "delivered"
    UNREACHABLE = "unreachable"
    OPTED_OUT = "opted_out"


@dataclass
class SendOutcome:
    """The meetups of one cleanup fan-out, bucketed by how the notice to their owner resolved."""

    delivered: list[Meetup] = field(default_factory=list)
    unreachable: list[Meetup] = field(default_factory=list)
    failed: list[Meetup] = field(default_factory=list)


def partition_by_opt_in(
    meetups: Sequence[Meetup], wants_notice: Callable[[Settings], bool]
) -> tuple[list[Meetup], list[Meetup]]:
    """The meetups whose owner still wants this notice, and those whose owner turned it off."""
    wanted: list[Meetup] = []
    opted_out: list[Meetup] = []
    for meetup in meetups:
        bucket = wanted if wants_notice(meetup.owner.settings) else opted_out
        bucket.append(meetup)
    return wanted, opted_out


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


def warning_due_time(meetup: Meetup) -> dt.datetime | None:
    """When the warning became due, or None when the meetup never expired."""
    expiration = meetup.expiration_time
    return None if expiration is None else as_utc(expiration) + owner_policy(meetup).deletion_warning_delay


def deletion_due_time(meetup: Meetup) -> dt.datetime | None:
    """The later of the two gates the deletion statement applies, or None when neither stamp is set.

    Retention runs from the deactivation stamp, and the owner is owed a full `deletion_warning_lead`
    from the moment their warning was recorded — so a meetup still inside that lead is waiting
    lawfully rather than running late, and measuring it against retention alone would report it as
    overdue for as long as the warning was delayed.
    """
    policy = owner_policy(meetup)
    gates = [
        as_utc(stamp) + duration
        for stamp, duration in (
            (meetup.expiration_time, policy.inactive_retention),
            (meetup.warned_time, policy.deletion_warning_lead),
        )
        if stamp is not None
    ]
    return max(gates) if gates else None


def days_overdue(due: dt.datetime | None) -> int | None:
    """Whole days the meetup has been eligible for its phase, or None when nothing dates it."""
    return None if due is None else (dt.datetime.now(dt.UTC) - due).days


def log_residue(event: str, meetup: Meetup, reason: ResidueReason, due: dt.datetime | None):
    """Record a nominated meetup its phase left behind, escalating one that stopped moving.

    A send that raised defers the meetup to the next run, which re-nominates and re-warns it at
    the same level forever; past `STUCK_RESIDUE_DAYS` the retry is not working and the daily
    warning is no longer news, so the row gets an error of its own instead. An unreachable owner
    is not a retry — the meetup is marked or deleted in the same run — so it never escalates.
    """
    overdue = days_overdue(due)
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


def log_residues(
    outcome: SendOutcome, due_of: Callable[[Meetup], dt.datetime | None], *, unreachable_event: str, failed_event: str
):
    """Record everything the phase left behind, deriving each meetup's due time through `due_of`."""
    for meetup in outcome.unreachable:
        log_residue(unreachable_event, meetup, ResidueReason.OWNER_UNREACHABLE, due_of(meetup))
    for meetup in outcome.failed:
        log_residue(failed_event, meetup, ResidueReason.OWNER_NOTIFICATION_FAILED, due_of(meetup))


def failed_meeting_properties(meetups: Sequence[Meetup]) -> dict[str, Any] | None:
    """EMF properties naming the meetings a run left behind, or None when it left none."""
    return {"failed_meeting_ids": [meetup.id for meetup in meetups]} if meetups else None


def deletion_warning_view(meetup: Meetup) -> MitupView:
    return MitupView(
        message=NotificationMessages.DELETION_WARNING.rich(
            lang=meetup.lang,
            meeting_title=title_content(meetup),
            # The message promises a deadline, so the lead has to be the owner's own: the free
            # policy's value is only right for a free owner.
            days_until_deletion=window_days(meetup, lambda policy: policy.deletion_warning_lead),
            past_meetings_button=ButtonMessages.PAST_MEETINGS.text(lang=meetup.user_language),
            reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.text(lang=meetup.user_language),
        ),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.text(lang=meetup.user_language),
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
        message=NotificationMessages.DELETED.rich(lang=meetup.lang, meeting_title=title_content(meetup)),
        menu=[],
    )


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


async def notify_meetups_about_to_be_deleted(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    """Warn every owner whose meeting is a week away from permanent deletion.

    An owner who has blocked the bot can never receive the warning, and one who turned the warning
    off does not want it, so either way the meeting is marked as warned and moves on to the
    deletion pool instead of being re-warned on every run. A send that raised leaves the meeting in
    the pool for the next run to retry.
    """
    meetups = (await session.exec(MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT)).all()
    wanted, opted_out = partition_by_opt_in(meetups, lambda settings: settings.deletion_warning)
    outcome = await notify_owners(api, wanted, deletion_warning_view)

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
    for meetup in opted_out:
        record_warning(meetup, WarningDelivery.OPTED_OUT)

    for meetup in outcome.delivered + outcome.unreachable + opted_out:
        meetup.record_deletion_warning()

    log.info(
        "Deletion warning sweep complete",
        nominated=len(meetups),
        delivered=len(outcome.delivered),
        unreachable=len(outcome.unreachable),
        opted_out=len(opted_out),
        failed=len(outcome.failed),
        windows=loggable_windows(lambda policy: policy.deletion_warning_delay),
        reason="warning_window_elapsed",
    )

    # This sweep never raises, so the run closes on Fault=0 whatever happened here: the counter is
    # the only alarmable trace of a warning that never reached its owner.
    metrics.emit(
        MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        len(outcome.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(outcome.failed),
    )


async def delete_meetups(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    """Permanently delete every meeting past its owner's `inactive_retention` and past a full
    `deletion_warning_lead` since its warning was recorded.

    Delivering the notice is not a precondition for the deletion: an owner who has blocked the
    bot has opted out of the notice, one who turned it off said so outright, and keeping their
    expired meetings alive to keep retrying it would retain the data forever. Only a send that
    raised, a transient Telegram failure, defers the deletion to the next run.
    """
    meetups = (await session.exec(MEETUPS_TO_DELETE_STATEMENT)).all()
    wanted, opted_out = partition_by_opt_in(meetups, lambda settings: settings.deletion_notice)
    outcome = await notify_owners(api, wanted, deletion_notice_view)

    log_residues(
        outcome,
        deletion_due_time,
        unreachable_event="Meeting deleted without notifying its owner",
        failed_event="Meeting deletion deferred",
    )

    deletable = outcome.delivered + outcome.unreachable + opted_out
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
        supporter_levels=supporter_level_counts(meetup.owner.supporter_level for meetup in deletable),
        windows=loggable_windows(lambda policy: policy.inactive_retention),
        unnotified=len(outcome.unreachable),
        opted_out=len(opted_out),
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
        opted_out=len(opted_out),
        failed=len(outcome.failed),
        purged=len(deletable),
        invitee_users_purged=len(outside_user_ids),
    )

    # Neither branch raises, so the run closes on Fault=0 either way. A meeting destroyed without its
    # owner ever being told, and one whose deletion was deferred by a failed notice, are both
    # irreversible enough to alarm on and invisible to every other series.
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
