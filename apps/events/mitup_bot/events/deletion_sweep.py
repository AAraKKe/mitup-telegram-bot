"""What the two meeting-deletion sweeps share.

A sweep reads its nomination, splits the owners behind it into chunks it commits one at a time,
writes one digest per owner, and records every meeting it left behind.
"""

import datetime as dt
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import partial
from itertools import batched
from typing import Any

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.views import MitupView

from .telemetry import error_type_name

log = structlog.get_logger(__name__)

# A residue row is expected to clear on the next daily run. Past this many days the sweep is not
# retrying any more, it is stuck, and repeating the same warning every day has stopped being news.
STUCK_RESIDUE_DAYS = 7

# One chunk of owners is one transaction: the most work a failure can lose, and the most digests
# that wait on a single commit.
OWNERS_PER_CHUNK = 20


class ResidueReason(StrEnum):
    """Why a nominated meetup did not get the notice its sweep intended to deliver."""

    OWNER_UNREACHABLE = "owner_unreachable"
    OWNER_NOTIFICATION_FAILED = "owner_notification_failed"


@dataclass
class NoticeOutcome:
    """The meetups of one chunk, bucketed by how the digest to their owner resolved, and by
    accumulation the totals of the sweep that chunk belongs to.

    An owner is written to once per chunk, so every meetup of one digest shares its outcome.
    """

    delivered: list[Meetup] = field(default_factory=list)
    unreachable: list[Meetup] = field(default_factory=list)
    failed: list[Meetup] = field(default_factory=list)
    opted_out: list[Meetup] = field(default_factory=list)
    invitees_purged: int = 0
    failed_chunks: int = 0

    @property
    def settled(self) -> list[Meetup]:
        """The meetups whose notice reached a terminal answer: the ones the sweep marks or deletes.

        A send that raised is the one bucket left out, because it is the only one worth retrying.
        """
        return self.delivered + self.unreachable + self.opted_out

    def merge(self, chunk: NoticeOutcome):
        """Fold a committed chunk into the sweep totals."""
        self.delivered += chunk.delivered
        self.unreachable += chunk.unreachable
        self.failed += chunk.failed
        self.opted_out += chunk.opted_out
        self.invitees_purged += chunk.invitees_purged


@dataclass(frozen=True, slots=True)
class Nomination:
    """What a sweep's opening read found: how many meetups its statement selected, and the owners
    behind them split into the chunks the sweep commits one at a time."""

    meetup_count: int
    chunks: list[list[int]]


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


def group_by_owner(meetups: Sequence[Meetup]) -> list[list[Meetup]]:
    """The nominated meetups of each owner, one group per owner, in the order they were nominated."""
    groups: dict[int, list[Meetup]] = defaultdict(list)
    for meetup in meetups:
        groups[meetup.owner.db_id].append(meetup)
    return list(groups.values())


def owner_chunks(meetups: Sequence[Meetup]) -> list[list[int]]:
    """The owners of the nominated meetups, in the order they were nominated, split into the chunks
    their sweep commits one at a time."""
    owner_ids = dict.fromkeys(meetup.owner.db_id for meetup in meetups)
    return [list(chunk) for chunk in batched(owner_ids, OWNERS_PER_CHUNK, strict=False)]


def owner_count(meetups: Sequence[Meetup]) -> int:
    """How many owners a bucket of meetups belongs to."""
    return len({meetup.owner.db_id for meetup in meetups})


def bucket_meetups(_user: User, *, bucket: list[Meetup], meetups: Sequence[Meetup]):
    bucket.extend(meetups)


def bucket_failed_meetups(_user: User, _error: Exception, *, bucket: list[Meetup], meetups: Sequence[Meetup]):
    bucket.extend(meetups)


async def notify_owners(
    api: TelegramApiWrapper,
    meetups: Sequence[Meetup],
    wants_notice: Callable[[Settings], bool],
    build_view: Callable[[Sequence[Meetup]], MitupView],
) -> NoticeOutcome:
    """Send every owner who still wants this notice one digest of their meetups; report how each resolved."""
    wanted, opted_out = partition_by_opt_in(meetups, wants_notice)
    outcome = NoticeOutcome(opted_out=opted_out)
    groups = group_by_owner(wanted)
    if not groups:
        return outcome

    await api.send_messages_to_users(
        users=[group[0].owner for group in groups],
        views=[build_view(group) for group in groups],
        on_success=[partial(bucket_meetups, bucket=outcome.delivered, meetups=group) for group in groups],
        on_unreachable=[partial(bucket_meetups, bucket=outcome.unreachable, meetups=group) for group in groups],
        on_error=[partial(bucket_failed_meetups, bucket=outcome.failed, meetups=group) for group in groups],
    )
    return outcome


def days_overdue(due: dt.datetime | None) -> int | None:
    """Whole days the meetup has been eligible for its sweep, or None when nothing dates it."""
    return None if due is None else (dt.datetime.now(dt.UTC) - due).days


def log_residue(event: str, meetup: Meetup, reason: ResidueReason, due: dt.datetime | None):
    """Record a nominated meetup its sweep left behind, escalating one that stopped moving.

    A send that raised defers the meetup to the next run, which re-nominates and re-warns it at
    the same level forever; past `STUCK_RESIDUE_DAYS` the retry is not working and the daily
    warning is no longer news, so the row gets an error of its own instead. An unreachable owner
    is not a retry, the meetup is marked or deleted in the same run, so it never escalates.
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
    outcome: NoticeOutcome, due_of: Callable[[Meetup], dt.datetime | None], *, unreachable_event: str, failed_event: str
):
    """Record everything the sweep left behind, deriving each meetup's due time through `due_of`."""
    for meetup in outcome.unreachable:
        log_residue(unreachable_event, meetup, ResidueReason.OWNER_UNREACHABLE, due_of(meetup))
    for meetup in outcome.failed:
        log_residue(failed_event, meetup, ResidueReason.OWNER_NOTIFICATION_FAILED, due_of(meetup))


def failed_meeting_properties(meetups: Sequence[Meetup]) -> dict[str, Any] | None:
    """EMF properties naming the meetings a run left behind, or None when it left none."""
    return {"failed_meeting_ids": [meetup.id for meetup in meetups]} if meetups else None


@db.with_session
async def nominate_owners(session: AsyncSession, statement: SelectOfScalar[Meetup]) -> Nomination:
    """Read what a sweep has to work through and split its owners into chunks, in a transaction of
    its own.

    Owner ids rather than rows: every chunk re-reads its own meetups under the transaction that
    marks or deletes them, so nothing decided here is applied to a row that has moved on since.
    """
    meetups = (await session.exec(statement)).all()
    return Nomination(meetup_count=len(meetups), chunks=owner_chunks(meetups))


async def sweep_chunks(
    nomination: Nomination, process: Callable[[list[int]], Awaitable[NoticeOutcome]]
) -> NoticeOutcome:
    """Run every chunk of owners through *process*, each in a transaction of its own, and total up
    the ones that committed.

    A chunk that raised keeps its owners for the next daily run and the sweep carries on with the
    next chunk, so a failure costs its own chunk rather than the rest of the sweep.
    """
    totals = NoticeOutcome()
    for owner_ids in nomination.chunks:
        try:
            totals.merge(await process(owner_ids))
        except Exception as error:
            totals.failed_chunks += 1
            log.exception(
                "Cleanup chunk failed",
                owners=len(owner_ids),
                error_type=error_type_name(error),
                exc_info=error,
                reason="chunk_transaction_failed",
            )
    return totals


def raise_for_failed_chunks(failed_chunks: int):
    """Report the chunks a sweep lost as the run's fault, once it has logged and counted the rest."""
    if failed_chunks:
        raise RuntimeError(f"Failed to commit {failed_chunks} cleanup chunks. Check logs for details.")
