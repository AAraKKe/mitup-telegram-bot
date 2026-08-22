"""In-process background queue for meeting-card refreshes.

A job is per meeting, not per card: whatever the worker eventually runs re-reads the meeting and
re-renders every card still tracked for it, so several changes to one meeting collapse into a
single refresh. Jobs are keyed on the meeting id and taken one at a time, and a job's key leaves
`pending` before it reads the database — a change committed after that read therefore enqueues a
fresh job instead of merging into the one already running, which is what makes the queue converge
on the meeting's latest state rather than stopping at whatever some earlier job saw.
"""

import asyncio
from dataclasses import dataclass, replace

import structlog

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.monitoring.outbound import qualified_type

log = structlog.get_logger(__name__)

# The queue is the memory bound on a runtime that never blocks its producers: submits come from
# post-commit code that must not wait, so a wedged worker is absorbed and reported here instead of
# growing without limit. Sized far above any realistic burst of distinct meetings.
MAX_PENDING = 500


@dataclass(frozen=True)
class MeetingRefresh:
    """One meeting's cards to re-render, optionally leaving a single card alone.

    `skip_message_db_id` names the card the submitting invocation already rendered itself.
    """

    meeting_id: int
    skip_message_db_id: int | None = None


def merge_skip(waiting: int | None, incoming: int | None) -> int | None:
    """The skip two coalescing jobs agree on: only a card both of them name survives.

    Two invocations that each rendered a different card — or one that rendered none at all —
    leave no card the merged refresh may pass over, because the disagreement means somebody is
    waiting on the very card the other would skip.
    """
    return waiting if waiting == incoming else None


def skipped_card(meeting: Meetup, skip_message_db_id: int | None) -> MessageModel | None:
    """The freshly loaded row for `skip_message_db_id`, or None when there is none to skip.

    It has to be the object out of `meeting.messages`: `Message.__eq__` is value-based over every
    field but the id, so `update_meeting_messages` recognises the card to pass over only when it
    is handed a row from the very list it iterates.
    """
    if skip_message_db_id is None:
        return None
    return next((card for card in meeting.messages if card.id == skip_message_db_id), None)


class RefreshQueue:
    """Coalescing queue of meeting-card refreshes, drained by a single worker.

    The api belongs to the queue alone: the worker re-enters `db.begin_write` on it and capture
    mode is per-instance, so an api shared with a handler would collide mid-invocation.
    """

    def __init__(self, api: TelegramApiWrapper, max_pending: int = MAX_PENDING):
        self.api = api
        self.max_pending = max_pending
        self.pending: dict[int, MeetingRefresh] = {}
        self.in_flight: set[int] = set()
        self.work_available = asyncio.Event()

    def submit(self, job: MeetingRefresh) -> bool:
        """Queue a refresh, coalescing it onto any job already waiting for the same meeting.
        Answers whether it was accepted; a queue at its cap drops it."""
        if job.meeting_id in self.in_flight:
            # The running job read the meeting before this change committed, so it may still put a
            # stale render over the card the submitter drew: nothing may be passed over.
            job = replace(job, skip_message_db_id=None)
        waiting = self.pending.get(job.meeting_id)
        if waiting is not None:
            job = replace(job, skip_message_db_id=merge_skip(waiting.skip_message_db_id, job.skip_message_db_id))
        elif len(self.pending) >= self.max_pending:
            log.warning("Meeting card refresh dropped", meeting_id=job.meeting_id, reason="queue_full")
            return False
        self.pending[job.meeting_id] = job
        self.work_available.set()
        return True

    def take(self) -> MeetingRefresh | None:
        """Claim the longest-waiting job, or None when nothing is waiting.

        The key leaves `pending` here, before `execute` reads the meeting — see the module
        docstring for why that ordering is the whole convergence argument.
        """
        job = next(iter(self.pending.values()), None)
        if job is not None:
            del self.pending[job.meeting_id]
            self.in_flight.add(job.meeting_id)
        if not self.pending:
            self.work_available.clear()
        return job

    async def execute(self, job: MeetingRefresh):
        """Re-render one meeting's cards in its own write-mode critical section.

        `begin_write` is what makes this inherit the rendering path whole — the custom-emoji
        retry, the not-modified suppression, the dead-message classification and the reconcile
        that drops the rows Telegram reported gone.
        """
        async with db.begin_write(self.api) as session:
            meeting = await Meetup.by_id(session, job.meeting_id)
            if meeting is None:
                return
            skipped = skipped_card(meeting, job.skip_message_db_id)
            await self.api.update_meeting_messages(
                meeting=meeting,
                current_message=skipped,
                skip_current=skipped is not None,
            )

    async def run_next(self):
        """Run one job to completion, holding its meeting in flight throughout. A failure ends
        that job only: the worker draining the rest must outlive any one meeting's refresh."""
        job = self.take()
        if job is None:
            return
        try:
            await self.execute(job)
        except Exception as error:
            log.exception("Meeting card refresh failed", meeting_id=job.meeting_id, error_type=qualified_type(error))
        finally:
            self.in_flight.discard(job.meeting_id)

    async def run_worker(self):
        """Drain the queue one job at a time for as long as the task lives; cancel it to stop."""
        while True:
            await self.work_available.wait()
            await self.run_next()


__queue: RefreshQueue | None = None


def configure(api: TelegramApiWrapper, max_pending: int = MAX_PENDING) -> RefreshQueue:
    """Build the process's refresh queue and publish it to `current_queue`.

    Every process entry point that refreshes meeting cards in the background calls this once at
    startup and spawns `run_worker` on the returned queue.
    """
    global __queue
    __queue = RefreshQueue(api, max_pending=max_pending)
    return __queue


def current_queue() -> RefreshQueue | None:
    """The process's refresh queue, or None where none was configured — a CLI job or a test
    renders its cards inline, and a submit there is a no-op rather than a failure."""
    return __queue
