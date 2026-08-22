"""In-process background queue for meeting-card refreshes.

A job is per meeting, not per card: whatever the worker eventually runs re-reads the meeting and
re-renders every card still tracked for it, so several changes to one meeting collapse into a
single refresh. Jobs are keyed on the meeting id and taken one at a time, and a job's key leaves
`pending` before it reads the database — a change committed after that read therefore enqueues a
fresh job instead of merging into the one already running, which is what makes the queue converge
on the meeting's latest state rather than stopping at whatever some earlier job saw.

The worker is two tasks over that one queue: a drain loop that takes jobs and runs them, and a
reporter that publishes the queue's accounting every `REPORT_INTERVAL_SECONDS`. They are separate
so the report is a clock rather than a consequence of the drain — a job that hangs can neither
delay a window nor silence one, which is what leaves `OldestJobAge` climbing on a series that never
stops flowing. Each job also runs under its own timeout, so the hang ends by itself: the job is
cancelled, counted failed, and the loop moves to the next meeting. Every window mints a `run_id`,
bound on the lines of the jobs that run under it and carried by the records that close it, which is
how a count pivots to the lines of the jobs behind it.

Stopping the worker is cancelling it. Because the queue lives in this process only, a job still
waiting at that point is a committed change no card will ever show, so a cancelled worker leaves
service, spends a bounded deadline draining what it holds, publishes the window the stop
interrupted, and names whatever the deadline cut short.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum, auto
from time import perf_counter
from typing import Any
from uuid import uuid4

import structlog
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, MeetingRefresh, TelegramApiWrapper, build_api
from mitup_bot.config import AppConfig
from mitup_bot.models import Meetup
from mitup_bot.models import Message as MessageModel
from mitup_bot.monitoring import EmfBackend, MetricKey, MetricsClient, MetricUnit, bound_metrics_client
from mitup_bot.monitoring.outbound import qualified_type

log = structlog.get_logger(__name__)

# The queue is the memory bound on a runtime that never blocks its producers: submits come from
# post-commit code that must not wait, so a wedged worker is absorbed and reported here instead of
# growing without limit. Sized far above any realistic burst of distinct meetings.
MAX_PENDING = 500

# How often the reporter closes a window and publishes it. It is the cadence of every series the
# worker owns, so it is also the resolution at which a queue going wrong becomes visible.
REPORT_INTERVAL_SECONDS = 60

# How long one job may run before it is cancelled and counted as failed. The default a process
# overrides from `AppConfig.background_job_timeout_seconds`, which documents the bound it sets.
JOB_TIMEOUT_SECONDS = 60.0

# How long a stopping worker spends on the jobs it is still holding. The default a process overrides
# from `AppConfig.background_drain_seconds`, which documents the bound it has to fit inside.
DRAIN_DEADLINE_SECONDS = 10.0

# The name the worker's database sessions are counted under. Sessions are attributed per context, so
# without it everything the worker holds falls into the unknown bucket of the leak counter, where a
# session the background jobs never returned cannot be told from anyone else's.
CONNECTION_CONTEXT = "BackgroundJobs"


class JobOutcome(StrEnum):
    """What one execution of a refresh job achieved."""

    REFRESHED = auto()
    SKIPPED = auto()
    FAILED = auto()


class SkipReason(StrEnum):
    """Why an execution had no cards to draw."""

    MEETING_GONE = auto()


class ScheduleOutcome(StrEnum):
    """Whether a submitted refresh became a job of its own or merged into one already waiting."""

    QUEUED = auto()
    COALESCED = auto()


class JobState(StrEnum):
    """Where an outstanding job stands: still waiting to be taken, or already running."""

    PENDING = auto()
    IN_FLIGHT = auto()


class DropReason(StrEnum):
    """Why a submitted refresh was refused outright."""

    QUEUE_FULL = auto()
    SHUTTING_DOWN = auto()


@dataclass(frozen=True)
class WorkerLimits:
    """The two clocks a process runs its background worker against.

    They travel together because neither reads on its own: `job_timeout` bounds one job and
    `drain_deadline` bounds every job left when the process stops, and it is the pair that says how
    long a teardown can take. A process takes both from its `AppConfig` so the two cannot drift
    between the runtimes that host the worker.
    """

    job_timeout: float = JOB_TIMEOUT_SECONDS
    drain_deadline: float = DRAIN_DEADLINE_SECONDS

    @classmethod
    def from_config(cls, app: AppConfig) -> WorkerLimits:
        return cls(job_timeout=app.background_job_timeout_seconds, drain_deadline=app.background_drain_seconds)


@dataclass(frozen=True)
class RunningJob:
    """A refresh the drain loop is executing, and the monotonic instant it began.

    The start is what an in-flight job's age is measured from, so a job holding the loop is
    reported while it still holds it rather than only once it ends one way or the other.
    """

    job: MeetingRefresh
    started: float


@dataclass(frozen=True)
class OutstandingJob:
    """The longest-outstanding job at publication time, with the age reported for it."""

    job: MeetingRefresh
    state: JobState
    age_ms: int


@dataclass
class TickCounters:
    """What the window in progress has accumulated so far, published and reset by `drain`."""

    queued_high_water: int = 0
    succeeded: int = 0
    failed: int = 0

    def record_depth(self, depth: int):
        self.queued_high_water = max(self.queued_high_water, depth)

    def drain(self, depth: int) -> TickCounters:
        """Detach what this window accumulated, counting *depth* as one last observation of it.

        A queue nobody submits to is still a queue nobody is draining, so the depth standing at
        publication time counts: a worker wedged inside one job keeps reporting the jobs waiting
        behind it rather than reporting the zero its lack of submits would otherwise produce.
        """
        self.record_depth(depth)
        published = TickCounters(self.queued_high_water, self.succeeded, self.failed)
        self.queued_high_water = 0
        self.succeeded = 0
        self.failed = 0
        return published


def merge_skip(waiting: int | None, incoming: int | None) -> int | None:
    """The skip two coalescing jobs agree on: only a card both of them name survives.

    Two invocations that each rendered a different card — or one that rendered none at all —
    leave no card the merged refresh may pass over, because the disagreement means somebody is
    waiting on the very card the other would skip.
    """
    return waiting if waiting == incoming else None


def coalesce(waiting: MeetingRefresh, incoming: MeetingRefresh) -> MeetingRefresh:
    """Merge *incoming* into the job already waiting for the same meeting.

    Everything identifying the waiting job survives the merge — its origin update, its enqueue
    time, its attempt — because that is the submit whose wait `queue_wait_ms` measures and whose
    user has been looking at a stale card the longest. Only the skip narrows and the count grows.
    """
    return replace(
        waiting,
        skip_message_db_id=merge_skip(waiting.skip_message_db_id, incoming.skip_message_db_id),
        coalesced=waiting.coalesced + 1,
    )


def skipped_card(meeting: Meetup, skip_message_db_id: int | None) -> MessageModel | None:
    """The freshly loaded row for `skip_message_db_id`, or None when there is none to skip.

    It has to be the object out of `meeting.messages`: `Message.__eq__` is value-based over every
    field but the id, so `update_meeting_messages` recognises the card to pass over only when it
    is handed a row from the very list it iterates.
    """
    if skip_message_db_id is None:
        return None
    return next((card for card in meeting.messages if card.id == skip_message_db_id), None)


def age_ms(now: float, since: float) -> int:
    return round((now - since) * 1000)


def elapsed_ms(since: float) -> int:
    return age_ms(perf_counter(), since)


def job_fields(job: MeetingRefresh, started: float) -> dict[str, Any]:
    """The facts every line about one execution carries: which refresh ran, and its two latencies.

    `queue_wait_ms` is how long the change sat unrendered before the worker reached it and
    `duration_ms` how long drawing it took, so a slow card and a backed-up queue stay tellable
    apart on a single line.
    """
    return {
        "meeting_id": job.meeting_id,
        "origin_update_id": job.origin_update_id,
        "coalesced": job.coalesced,
        "attempt": job.attempt,
        "queue_wait_ms": age_ms(started, job.enqueued_at),
        "duration_ms": elapsed_ms(started),
    }


class RefreshQueue:
    """Coalescing queue of meeting-card refreshes, drained by a single worker.

    The api belongs to the queue alone: the worker re-enters `db.begin_write` on it and capture
    mode is per-instance, so an api shared with a handler would collide mid-invocation. Owning it
    outright is also what lets the queue take the card text off its failure lines. *metrics* is the
    client that api emits through, which is what lands a job's outbound timings and its post-commit
    faults in the same window that publishes the job's counts.

    *accepts_fanout* is the deployed switch behind the deferral: with it off a committed fan-out
    draws every card on its own timeline and nothing is submitted here.
    """

    def __init__(
        self,
        api: TelegramApiWrapper,
        metrics: MetricsClient,
        max_pending: int = MAX_PENDING,
        report_interval: float = REPORT_INTERVAL_SECONDS,
        job_timeout: float = JOB_TIMEOUT_SECONDS,
        drain_deadline: float = DRAIN_DEADLINE_SECONDS,
        accepts_fanout: bool = True,
    ):
        self.api = api
        self.api.log_card_text = False
        self.metrics = metrics
        self.max_pending = max_pending
        self.report_interval = report_interval
        self.job_timeout = job_timeout
        self.drain_deadline = drain_deadline
        self.accepts_fanout = accepts_fanout
        self.pending: dict[int, MeetingRefresh] = {}
        self.in_flight: dict[int, RunningJob] = {}
        self.counters = TickCounters()
        self.work_available = asyncio.Event()
        self.accepting = True
        # The reporting window currently open. A job binds whichever one is open when it starts and
        # the reporter mints the next one as it publishes, so an id names a window, never a job.
        self.run_id = uuid4().hex

    def submit(self, job: MeetingRefresh) -> bool:
        """Queue a refresh, coalescing it onto any job already waiting for the same meeting.
        Answers whether it was accepted; a queue at its cap or one already stopping drops it."""
        if not self.accepting:
            # Nothing taken now could still be drawn: the worker is spending its last seconds on the
            # jobs it already holds, so accepting this one would only lengthen what it abandons.
            log.warning("Meeting card refresh dropped", meeting_id=job.meeting_id, reason=DropReason.SHUTTING_DOWN)
            return False
        if job.meeting_id in self.in_flight:
            # The running job read the meeting before this change committed, so it may still put a
            # stale render over the card the submitter drew: nothing may be passed over.
            job = replace(job, skip_message_db_id=None)
        waiting = self.pending.get(job.meeting_id)
        if waiting is not None:
            job = coalesce(waiting, job)
        elif len(self.pending) >= self.max_pending:
            log.warning("Meeting card refresh dropped", meeting_id=job.meeting_id, reason=DropReason.QUEUE_FULL)
            return False
        self.pending[job.meeting_id] = job
        self.counters.record_depth(len(self.pending))
        self.work_available.set()
        self.report_scheduled(job, merged=waiting is not None)
        return True

    def report_scheduled(self, job: MeetingRefresh, merged: bool):
        """Record the accepted submit. The enqueuing update is ambient wherever a submit happens —
        it comes from code running under one — so the line names the meeting and the queue only."""
        log.info(
            "Background jobs scheduled",
            meeting_id=job.meeting_id,
            outcome=ScheduleOutcome.COALESCED if merged else ScheduleOutcome.QUEUED,
            pending=len(self.pending),
        )

    def take(self) -> RunningJob | None:
        """Claim the longest-waiting job, or None when nothing is waiting.

        The key leaves `pending` here, before `execute` reads the meeting — see the module
        docstring for why that ordering is the whole convergence argument.
        """
        job = next(iter(self.pending.values()), None)
        running = None
        if job is not None:
            del self.pending[job.meeting_id]
            running = RunningJob(job, perf_counter())
            self.in_flight[job.meeting_id] = running
        if not self.pending:
            self.work_available.clear()
        return running

    def requeue(self, job: MeetingRefresh):
        """Put an interrupted job back among the waiting ones, counting the attempt against it.

        Its cards were left half drawn or not drawn at all, so the change stays outstanding —
        whether the drain that follows still reaches it or the shutdown has to name it among the
        ones nobody will. A refresh submitted for this meeting meanwhile is left alone: it already
        covers every card this one would have drawn.
        """
        self.pending.setdefault(job.meeting_id, replace(job, attempt=job.attempt + 1))
        self.work_available.set()

    async def execute(self, job: MeetingRefresh) -> JobOutcome:
        """Re-render one meeting's cards in its own write-mode critical section.

        `begin_write` is what makes this inherit the rendering path whole — the custom-emoji
        retry, the not-modified suppression, the dead-message classification and the reconcile
        that drops the rows Telegram reported gone.
        """
        async with db.begin_write(self.api) as session:
            meeting = await Meetup.by_id(session, job.meeting_id)
            if meeting is None:
                return JobOutcome.SKIPPED
            skipped = skipped_card(meeting, job.skip_message_db_id)
            await self.api.update_meeting_messages(
                meeting=meeting,
                current_message=skipped,
                skip_current=skipped is not None,
            )
        return JobOutcome.REFRESHED

    async def run_job(self, running: RunningJob) -> JobOutcome:
        """Execute one job under its own timeout and record how it ended, never raising.

        A job that outruns `job_timeout` is cancelled and counted like any other failure: the loop
        takes one job at a time, so a refresh that will never finish would otherwise hold every
        other meeting's cards behind it for as long as the process lives. A card Telegram refused
        is not counted here — the post-commit drain inside `execute` owns that failure and reports
        it on `PostCommitApiFault`, and counting it again would make the failed count read as jobs
        that never landed at all.
        """
        job, started = running.job, running.started
        try:
            async with asyncio.timeout(self.job_timeout):
                outcome = await self.execute(job)
        except Exception as error:
            self.counters.failed += 1
            log.exception(
                "Background job failed",
                **job_fields(job, started),
                outcome=JobOutcome.FAILED,
                error_type=qualified_type(error),
            )
            return JobOutcome.FAILED
        self.counters.succeeded += 1
        fields = job_fields(job, started)
        if outcome is JobOutcome.SKIPPED:
            fields["reason"] = SkipReason.MEETING_GONE
        log.info("Background job finished", **fields, outcome=outcome)
        return outcome

    async def run_next(self):
        """Run one job to completion, holding its meeting in flight throughout.

        A failure ends that job only: the drain loop behind it must outlive any one meeting's
        refresh. A cancellation ends the loop as well, and puts the job it interrupted back among
        the waiting ones for the shutdown drain to attempt again.
        """
        running = self.take()
        if running is None:
            return
        with structlog.contextvars.bound_contextvars(run_id=self.run_id):
            try:
                outcome = await self.run_job(running)
            except asyncio.CancelledError:
                self.requeue(running.job)
                raise
            finally:
                del self.in_flight[running.job.meeting_id]
                self.metrics.emit(MetricKey.JOB_PROCESSING_TIME, elapsed_ms(running.started), MetricUnit.MILLISECONDS)
            # A cancelled job reached no outcome and reports none: the fault rate is a rate over the
            # samples that carry it, so a shutdown would otherwise read as a burst of faults.
            self.metrics.emit(MetricKey.FAULT, float(outcome is JobOutcome.FAILED))

    def oldest_job(self) -> OutstandingJob | None:
        """The refresh outstanding longest right now, or None when the queue is idle.

        A job the loop is holding is measured from the moment it started running, which is what
        makes its age comparable with `job_timeout`; a job still waiting is measured from its
        submit, which is what a drain loop that stopped taking work looks like from here.
        """
        now = perf_counter()
        outstanding = [
            OutstandingJob(running.job, JobState.IN_FLIGHT, age_ms(now, running.started))
            for running in self.in_flight.values()
        ]
        outstanding.extend(
            OutstandingJob(job, JobState.PENDING, age_ms(now, job.enqueued_at)) for job in self.pending.values()
        )
        if not outstanding:
            return None
        return max(outstanding, key=lambda outstanding_job: outstanding_job.age_ms)

    def emit_window(self, tick: TickCounters, oldest: OutstandingJob | None):
        """Write the window's four records, every window, whatever the queue is doing.

        `run_id` rides each record as a property rather than through `set_global_property`, which
        would pin one window's id onto every record the process writes afterwards. The reporter is
        its only writer: a property is last-writer-wins over the whole flush window, so an id
        stamped by one job would be reported as describing every other job in the window too.
        """
        properties = {"run_id": self.run_id}
        self.metrics.emit(MetricKey.JOBS_QUEUED, tick.queued_high_water, properties=properties)
        self.metrics.emit(MetricKey.JOBS_SUCCEEDED, tick.succeeded, properties=properties)
        self.metrics.emit(MetricKey.JOBS_FAILED, tick.failed, properties=properties)
        self.metrics.emit(
            MetricKey.OLDEST_JOB_AGE,
            oldest.age_ms if oldest is not None else 0,
            MetricUnit.MILLISECONDS,
            properties=properties,
        )

    def report(self):
        """Publish the open window's accounting: its counts, its oldest job, and the lines for both.

        The age is a value on a series that flows whatever the drain loop is doing, so the line
        beside it has to name the job the value is about — without it the alarm says a job is old
        and nothing says which meeting is waiting on it.
        """
        tick = self.counters.drain(len(self.pending))
        oldest = self.oldest_job()
        self.emit_window(tick, oldest)
        log.info(
            "Background job drain finished",
            succeeded=tick.succeeded,
            failed=tick.failed,
            queued_high_water=tick.queued_high_water,
            pending=len(self.pending),
        )
        if oldest is not None:
            log.info(
                "Background job still outstanding",
                meeting_id=oldest.job.meeting_id,
                origin_update_id=oldest.job.origin_update_id,
                state=oldest.state,
                age_ms=oldest.age_ms,
            )

    async def publish(self):
        """Close the window that is open and open the next one.

        The records and the lines of a window both carry the id it ran under, so the next id is
        minted only once the flush that ends this one has returned.
        """
        with structlog.contextvars.bound_contextvars(run_id=self.run_id):
            self.report()
            await self.metrics.flush()
        self.run_id = uuid4().hex

    async def run_reporter(self):
        """Publish on the clock for as long as the worker lives, whatever the drain loop is doing.

        This is a task of its own precisely so no job can hold it up: a window that closed only
        when the drain loop came back would go quiet exactly when a stuck job made it interesting,
        and a series that stops says nothing about why.
        """
        while True:
            await asyncio.sleep(self.report_interval)
            await self.publish()

    async def run_drain(self):
        """Take jobs and run them for as long as the worker lives, waiting while there are none.

        The client is bound for the loop rather than per job, so the Telegram round-trips and pool
        samples a job raises land in whichever window ends up flushing them.
        """
        with bound_metrics_client(self.metrics):
            while True:
                await self.work_available.wait()
                await self.run_next()

    async def drain_pending(self):
        """Run what the queue is still holding, for at most `drain_deadline` seconds, then close
        the window the stop interrupted.

        The queue lives in this process only, so a job left waiting is a committed change no card
        will ever show — worth finishing, but only against a clock: the orchestrator that asked us
        to stop is running its own kill timer, and a drain that outlasts it takes the teardown and
        the line explaining the loss down with it. The reporter was cancelled with the group, so
        this publication is what carries out the counts and samples of every job in the window,
        the ones this drain just ran included.
        """
        self.accepting = False
        with bound_metrics_client(self.metrics):
            with suppress(TimeoutError):
                async with asyncio.timeout(self.drain_deadline):
                    while self.pending:
                        await self.run_next()
        await self.publish()

    async def run_worker(self):
        """Drain the queue and report on it until cancelled; the cancellation is the stop request.

        The drain and the reporter are siblings under one group, so cancelling the worker cancels
        both, and neither can outlive the other as half a worker — draining with nothing reporting,
        or reporting on a drain that stopped. Stopping then takes the queue out of service and
        spends the drain deadline on the jobs it still holds, the interrupted one included.
        """
        db.set_connection_context(CONNECTION_CONTEXT)
        try:
            async with asyncio.TaskGroup() as workers:
                workers.create_task(self.run_drain())
                workers.create_task(self.run_reporter())
        except asyncio.CancelledError:
            await self.drain_pending()
            raise
        finally:
            self.report_abandoned()

    def report_abandoned(self):
        """Name what the stopping worker is dropping. The queue lives in this process only, so a
        job still waiting when the task ends is a committed change no card will ever show."""
        if not self.pending:
            return
        log.warning(
            "Background jobs abandoned at shutdown",
            abandoned=len(self.pending),
            in_flight=len(self.in_flight),
        )


__queue: RefreshQueue | None = None


def configure(
    api: TelegramApiWrapper,
    metrics: MetricsClient,
    max_pending: int = MAX_PENDING,
    job_timeout: float = JOB_TIMEOUT_SECONDS,
    drain_deadline: float = DRAIN_DEADLINE_SECONDS,
    accepts_fanout: bool = True,
) -> RefreshQueue:
    """Build the process's refresh queue and publish it to `current_queue`.

    Every process entry point that refreshes meeting cards in the background calls this once at
    startup and spawns `run_worker` on the returned queue. *metrics* is the client *api* emits
    through, so everything one job produces reaches CloudWatch in the window that counts the job.
    """
    global __queue
    __queue = RefreshQueue(
        api,
        metrics,
        max_pending=max_pending,
        job_timeout=job_timeout,
        drain_deadline=drain_deadline,
        accepts_fanout=accepts_fanout,
    )
    return __queue


def configure_worker(bot: ExtBot, limits: WorkerLimits, accepts_fanout: bool = True) -> RefreshQueue:
    """Build the process's refresh queue over an api and a metrics client of its own.

    Both are the worker's alone. Capture mode is per-instance state on the api, so one shared with a
    handler or a recurrent event would collide mid-invocation; and the client is flushed at the end
    of every window, which is a cadence nothing else in the process shares — a client borrowed from
    a request or a run would carry the worker's samples out on somebody else's flush and vice versa.
    """
    metrics = MetricsClient(EmfBackend())
    return configure(
        build_api(BotAdapter(bot, metrics)),
        metrics,
        job_timeout=limits.job_timeout,
        drain_deadline=limits.drain_deadline,
        accepts_fanout=accepts_fanout,
    )


async def stop_worker(worker: asyncio.Task[None]):
    """Stop *worker* and wait out the drain its cancellation runs.

    Cancelling is how the worker is asked to stop and awaiting it is what holds the caller until the
    jobs it drains are drawn, so this must run while the resources those edits need are still up.
    The `CancelledError` the task ends on is the stop we asked for, not a failure of the caller.
    """
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


def current_queue() -> RefreshQueue | None:
    """The process's refresh queue, or None where none was configured — a CLI job or a test
    renders its cards inline, and a submit there is a no-op rather than a failure."""
    return __queue
