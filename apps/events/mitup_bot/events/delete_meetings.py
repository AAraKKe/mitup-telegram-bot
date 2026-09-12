from functools import partial
from typing import cast

import structlog
from sqlmodel import and_, col, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.deletion import purge_meetups
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
from .telemetry import supporter_level_counts

log = structlog.get_logger(__name__)

# Both windows depend on the owner's tier, so the statement joins the owner. The deletion runs from
# `expiration_time` but additionally waits out the lead from `warned_time`, so it can never land
# sooner than the warning promised, however late that warning was issued. A row whose `warned_time`
# is NULL satisfies neither comparison.
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


@db.with_session
async def delete_owner_chunk(session: AsyncSession, api: TelegramApiWrapper, owner_ids: list[int]) -> NoticeOutcome:
    """Tell one chunk of owners which of their meetings is going, then delete those meetings and
    the invitees that exist only inside them.

    The notices go out before the deletes and both commit together, so a failure can only leave
    behind a meeting the next run deletes after telling its owner once more.
    """
    meetups = (await session.exec(MEETUPS_TO_DELETE_STATEMENT.where(col(User.id).in_(owner_ids)))).all()
    outcome = await notify_owners(
        api, meetups, lambda settings: settings.deletion_notice, meeting_views.deletion_notice_view
    )

    log_residues(
        outcome,
        lambda meetup: meetup.deletion_due_time,
        unreachable_event="Meeting deleted without notifying its owner",
        failed_event="Meeting deletion deferred",
    )

    deletable = outcome.settled

    # Named before the DELETE: after it nothing can reconstruct which meetings a run took.
    log.info(
        "Meetups purged",
        count=len(deletable),
        owners=owner_count(deletable),
        meeting_ids=[cast(int, meetup.id) for meetup in deletable],
        supporter_levels=supporter_level_counts(meetup.owner.supporter_level for meetup in deletable),
        windows=loggable_windows(lambda policy: policy.inactive_retention),
        unnotified=len(outcome.unreachable),
        opted_out=len(outcome.opted_out),
        reason="retention_elapsed",
    )
    outcome.invitees_purged = len(await purge_meetups(session, deletable))

    return outcome


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Permanently delete every meeting past its owner's `inactive_retention` and past a full
    `deletion_warning_lead` since its warning was recorded.

    Delivering the notice is not a precondition for the deletion: an owner who has blocked the
    bot has opted out of the notice, one who turned it off said so outright, and keeping their
    expired meetings alive to keep retrying it would retain the data forever. Only a send that
    raised, a transient Telegram failure, defers the deletion to the next run, and it defers every
    meeting of that owner's digest.
    """
    nomination = await nominate_owners(MEETUPS_TO_DELETE_STATEMENT)
    totals = await sweep_chunks(nomination, partial(delete_owner_chunk, api))

    log.info(
        "Meetup purge sweep complete",
        nominated=nomination.meetup_count,
        delivered=len(totals.delivered),
        unreachable=len(totals.unreachable),
        opted_out=len(totals.opted_out),
        failed=len(totals.failed),
        owners_messaged=owner_count(totals.delivered),
        owners_unreachable=owner_count(totals.unreachable),
        owners_opted_out=owner_count(totals.opted_out),
        owners_failed=owner_count(totals.failed),
        purged=len(totals.settled),
        invitee_users_purged=totals.invitees_purged,
        chunks=len(nomination.chunks),
        failed_chunks=totals.failed_chunks,
    )

    # Neither branch raises, so a sweep whose chunks all committed closes on Fault=0 either way. A
    # meeting destroyed without its owner ever being told, and one whose deletion was deferred by a
    # failed notice, are both irreversible enough to alarm on and invisible to every other series.
    metrics.emit(MetricKey.MEETUPS_DELETED_UNNOTIFIED, len(totals.unreachable), MetricUnit.COUNT)
    metrics.emit(
        MetricKey.MEETINGS_DELETION_FAILED,
        len(totals.failed),
        MetricUnit.COUNT,
        properties=failed_meeting_properties(totals.failed),
    )
    raise_for_failed_chunks(totals.failed_chunks)
