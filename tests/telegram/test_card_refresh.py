import asyncio
from contextlib import suppress
from time import perf_counter
from typing import cast
from unittest import mock

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram.error import BadRequest
from telegram.ext import ExtBot

from mitup_bot import card_refresh, reconcile
from mitup_bot.api_wrapper import BotAdapter, MeetingRefresh, TelegramApi
from mitup_bot.card_refresh import JobOutcome, RefreshQueue, RunningJob, WorkerLimits
from mitup_bot.config import AppConfig, RunModes
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.protocols import ContextOrBotAdapter
from tests.helpers.fixtures import create_meetup, create_message, create_user
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

WORKER_TIMEOUT = 5.0
# Long enough that a window reliably reaches the jobs already waiting when it opens, short enough
# that a test waiting one out costs nothing.
TICK_INTERVAL = 0.05
# Short enough that a test can wait out a job's timeout, long enough that a job doing nothing but
# returning never hits it. Only the test about the timeout uses it: everywhere else a job is
# allowed to block for as long as the test needs, so the timeout never lands mid-assertion.
JOB_TIMEOUT = 0.1
# What a stopping queue spends on the jobs it still holds. Short enough that a test whose job never
# finishes waits it out for nothing, long enough for one that does to be reached.
DRAIN_DEADLINE = 0.2


@pytest.fixture
def bot() -> mock.AsyncMock:
    return mock.AsyncMock(spec=ExtBot)


@pytest.fixture
def refresh_api(bot: mock.AsyncMock, metrics_client: MetricsClient) -> TelegramApi:
    """The api the queue owns outright: the worker re-enters capture mode on it, and capture
    mode is per-instance, so it can never be one a handler is also using."""
    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, BotAdapter(bot=cast(ExtBot, bot), metrics=metrics_client))
    return api


@pytest.fixture
def queue(refresh_api: TelegramApi, metrics_client: MetricsClient) -> RefreshQueue:
    return RefreshQueue(
        refresh_api,
        metrics_client,
        report_interval=TICK_INTERVAL,
        job_timeout=WORKER_TIMEOUT,
        drain_deadline=DRAIN_DEADLINE,
    )


def mark_in_flight(queue: RefreshQueue, meeting_id: int):
    """Hold a meeting in flight the way `take` does, for tests that never run the drain loop."""
    queue.in_flight[meeting_id] = RunningJob(MeetingRefresh(meeting_id=meeting_id), perf_counter())


def emitted_values(client: MetricsClient, name: MetricKey) -> list[float]:
    """Every value recorded under *name*, in emission order, for assertions about how one moves."""
    return [record.value for record in client.records if record.name == str(name)]


@pytest.fixture
def registered_reconciler() -> None:
    """`begin_write` refuses to start a critical section without one."""
    reconcile.register_outbox_reconciler()


@pytest.fixture
def meeting_with_two_cards() -> Meetup:
    """A meeting tracked by the owner's own card and by one shared inline card."""
    meeting = create_meetup(id=7, title="Refresh me", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    meeting.messages = [
        create_message(id=41, inline_message_id=None, chat_id=100, message_id=501, meetup_id=7),
        create_message(
            id=42,
            inline_message_id="inline_shared",
            chat_instance="ci",
            chat_id=None,
            message_id=None,
            meetup_id=7,
        ),
    ]
    return meeting


# --- Coalescing and the merge rules ---


def test_submit_keeps_one_job_per_meeting(queue: RefreshQueue):
    assert queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))
    assert queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))
    assert queue.submit(MeetingRefresh(meeting_id=8))

    assert list(queue.pending) == [7, 8]


@pytest.mark.parametrize(
    "waiting_skip, incoming_skip, merged_skip",
    [
        (41, 41, 41),
        (41, 42, None),
        (41, None, None),
        (None, 41, None),
    ],
    ids=["same_card", "different_cards", "incoming_skips_nothing", "waiting_skips_nothing"],
)
def test_conflicting_skips_coalesce_to_no_skip(
    queue: RefreshQueue, waiting_skip: int | None, incoming_skip: int | None, merged_skip: int | None
):
    """A card only one of the two invocations rendered is still stale to the other, so the
    merged job may pass over nothing but a card both of them named."""
    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=waiting_skip))
    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=incoming_skip))

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=merged_skip, coalesced=1)


def test_a_merged_job_keeps_the_origin_and_the_wait_of_the_submit_it_merged_into(queue: RefreshQueue):
    """The merged job answers for the change that has been waiting longest, so it keeps that
    submit's update and enqueue time: `queue_wait_ms` measures the oldest unrendered change, and
    the pivot leads to the update whose user has been looking at a stale card since."""
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))
    first = queue.pending[7]

    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=9999))

    merged = queue.pending[7]
    assert merged.origin_update_id == 4242
    assert merged.enqueued_at == first.enqueued_at
    assert merged.coalesced == 1


def test_a_skip_is_not_honoured_while_the_meeting_is_in_flight(queue: RefreshQueue):
    """The running job read the meeting before this change committed, so it may still lay a
    stale render over the very card the submitter drew: that card needs refreshing too."""
    mark_in_flight(queue, 7)

    assert queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=None)


def test_an_in_flight_meeting_drops_the_skip_before_coalescing(queue: RefreshQueue):
    """Order matters: dropping the skip first is what stops two submits that agree on a card
    from agreeing their way past the in-flight rule."""
    mark_in_flight(queue, 7)
    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=None, coalesced=1)


# --- The pending cap ---


def test_the_pending_cap_drops_a_new_meeting_and_reports_it(refresh_api: TelegramApi, metrics_client: MetricsClient):
    """Submits come from post-commit code that must not block, so the cap absorbs a wedged
    worker — but silently losing a committed change is exactly what the line has to prevent."""
    queue = RefreshQueue(refresh_api, metrics_client, max_pending=2)
    queue.submit(MeetingRefresh(meeting_id=1))
    queue.submit(MeetingRefresh(meeting_id=2))

    with capture_logs() as logs:
        accepted = queue.submit(MeetingRefresh(meeting_id=3))

    assert accepted is False
    assert list(queue.pending) == [1, 2]
    dropped = [entry for entry in logs if entry["event"] == "Meeting card refresh dropped"]
    assert len(dropped) == 1
    assert dropped[0]["log_level"] == "warning"
    assert dropped[0]["reason"] == "queue_full"
    assert dropped[0]["meeting_id"] == 3
    # A refusal that also announced itself as scheduled would be counted as work the worker owes.
    assert not [entry for entry in logs if entry["event"] == "Background jobs scheduled"]


def test_the_pending_cap_still_coalesces_onto_a_waiting_meeting(
    refresh_api: TelegramApi, metrics_client: MetricsClient
):
    """The cap bounds distinct meetings; refusing a merge would discard a committed change that
    costs no extra entry."""
    queue = RefreshQueue(refresh_api, metrics_client, max_pending=2)
    queue.submit(MeetingRefresh(meeting_id=1, skip_message_db_id=41))
    queue.submit(MeetingRefresh(meeting_id=2))

    assert queue.submit(MeetingRefresh(meeting_id=1, skip_message_db_id=42))

    assert queue.pending[1] == MeetingRefresh(meeting_id=1, skip_message_db_id=None, coalesced=1)


# --- Taking a job ---


def test_take_claims_jobs_oldest_first_and_marks_them_in_flight(queue: RefreshQueue):
    """Taking a job is also what starts the clock the oldest-job age reads it by, so the claim
    and the start instant have to be the same act."""
    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    taken = queue.take()

    assert taken is not None
    assert taken.job == MeetingRefresh(meeting_id=7)
    assert queue.in_flight == {7: taken}
    assert taken.started >= taken.job.enqueued_at
    assert list(queue.pending) == [8]
    next_taken = queue.take()
    assert next_taken is not None
    assert next_taken.job == MeetingRefresh(meeting_id=8)
    assert queue.take() is None


async def test_the_key_leaves_pending_before_the_meeting_is_read(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    meeting_with_two_cards: Meetup,
):
    """The convergence argument rests on this ordering: a change committing while a job reads
    the meeting must be able to queue a job of its own. Were the key still in `pending` at read
    time the two would coalesce, and the queue would settle on the older job's view of the
    meeting with the newer change never rendered."""
    observed_pending: list[dict[int, MeetingRefresh]] = []

    async def observe_then_load(session: object, meetup_id: int) -> Meetup:
        observed_pending.append(dict(queue.pending))
        queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))
        return meeting_with_two_cards

    queue.submit(MeetingRefresh(meeting_id=7))
    with mock.patch.object(Meetup, "by_id", side_effect=observe_then_load):
        await queue.run_next()

    assert observed_pending == [{}]
    # The concurrent submit survived as a job of its own, with its skip dropped because the
    # meeting was in flight when it arrived.
    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=None)
    assert queue.in_flight == {}


# --- Executing a job ---


async def test_execute_renders_every_card_and_reconciles_the_dead_one(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
    meeting_with_two_cards: Meetup,
):
    """Load, render, edit and dead-row reconcile against the real api: the queue re-implements
    none of it, so a card Telegram reports gone is classified by the drain and its row deleted
    by the write lifecycle's reconcile transaction."""
    mock_session.add_object(meeting_with_two_cards)
    owner_card, shared_card = meeting_with_two_cards.messages
    bot.edit_message_text.side_effect = [BadRequest("Message to edit not found"), None]

    await queue.execute(MeetingRefresh(meeting_id=7))

    assert bot.edit_message_text.await_count == 2
    # The text came off the loaded meeting, so the render read the DB rather than anything the
    # job carried, and it reached both the owner's card and the shared one.
    assert "Refresh me" in bot.edit_message_text.call_args_list[0].kwargs["text"]
    assert bot.edit_message_text.call_args_list[1].kwargs["inline_message_id"] == shared_card.inline_message_id
    delete_queries = [query for query in mock_session.queries_executed if query.startswith("DELETE FROM messages")]
    assert len(delete_queries) == 1
    assert f"messages.id IN ({owner_card.id})" in delete_queries[0]


async def test_execute_passes_over_the_card_the_submitter_already_rendered(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
    meeting_with_two_cards: Meetup,
):
    """The skip row is resolved out of the freshly loaded `meeting.messages`: `Message.__eq__`
    is value-based over every field but the id, so `update_meeting_messages` recognises the card
    to pass over only when handed a row from the list it iterates."""
    mock_session.add_object(meeting_with_two_cards)
    owner_card, shared_card = meeting_with_two_cards.messages

    await queue.execute(MeetingRefresh(meeting_id=7, skip_message_db_id=owner_card.id))

    bot.edit_message_text.assert_awaited_once()
    assert bot.edit_message_text.call_args.kwargs["inline_message_id"] == shared_card.inline_message_id


async def test_execute_refreshes_every_card_when_the_skipped_one_is_gone(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
    meeting_with_two_cards: Meetup,
):
    """A card deleted between submit and run leaves nothing to skip; refreshing the rest is the
    safe reading, since the submitter's own render went with the row."""
    mock_session.add_object(meeting_with_two_cards)

    await queue.execute(MeetingRefresh(meeting_id=7, skip_message_db_id=999))

    assert bot.edit_message_text.await_count == 2


async def test_execute_is_a_no_op_when_the_meeting_is_gone(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
):
    """A meeting deleted between submit and run has no cards left to draw and must not raise."""
    assert await queue.execute(MeetingRefresh(meeting_id=7)) is JobOutcome.SKIPPED

    bot.edit_message_text.assert_not_awaited()


async def test_a_job_draws_its_own_cards_rather_than_queueing_the_meeting_again(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
    meeting_with_two_cards: Meetup,
):
    """The worker's api carries no queue, so a job cannot defer its fan-out back to the queue
    running it — which it would then do again on every execution, for the life of the process."""
    mock_session.add_object(meeting_with_two_cards)
    assert queue.api.refresh_queue is None

    await queue.execute(MeetingRefresh(meeting_id=7))

    assert bot.edit_message_text.await_count == 2
    assert queue.pending == {}


async def test_a_failed_card_edit_reports_the_card_by_size(
    queue: RefreshQueue,
    mock_session: MockDbSession,
    registered_reconciler: None,
    bot: mock.AsyncMock,
    meeting_with_two_cards: Meetup,
):
    """A fanout multiplies a failure line by every card tracking the meeting, and a card is the
    title, description and participants its users wrote: the queue's api reports how big the card
    was and never what it said."""
    mock_session.add_object(meeting_with_two_cards)
    bot.edit_message_text.side_effect = BadRequest("Bad Request: message can't be edited")

    with capture_logs() as logs:
        await queue.execute(MeetingRefresh(meeting_id=7))

    failures = [entry for entry in logs if entry["event"] == "Queued Telegram call failed after commit"]
    assert len(failures) == 2
    payload = failures[0]["payload"]
    assert set(payload) == {"chat_id", "message_id", "inline_message_id", "text_len", "entity_count"}
    assert payload["text_len"] > 0


# --- The worker ---


async def test_run_next_does_nothing_when_the_queue_is_empty(
    queue: RefreshQueue, bot: mock.AsyncMock, metrics: MetricAssertions
):
    """The worker re-checks the queue after every wake, so an empty take has to be a clean
    no-op rather than a job that is not there."""
    await queue.run_next()

    bot.edit_message_text.assert_not_awaited()
    assert queue.in_flight == {}
    # A take that found nothing timed nothing: the sample would be a zero the p90 has to carry.
    metrics.assert_not_emitted(name=MetricKey.JOB_PROCESSING_TIME)
    # And it is no unit of work either: a sample here would add to the denominator the fault rate
    # divides by, diluting the rate with wakes that ran nothing.
    metrics.assert_not_emitted(name=MetricKey.FAULT)


async def test_the_worker_drains_the_queue_and_survives_a_failing_job(queue: RefreshQueue):
    """One meeting's failure must not take the worker with it: every other meeting's cards are
    waiting on the same single drain."""
    drained = asyncio.Event()
    executed: list[int] = []

    async def execute(job: MeetingRefresh) -> JobOutcome:
        executed.append(job.meeting_id)
        if job.meeting_id == 7:
            raise RuntimeError("render blew up")
        drained.set()
        return JobOutcome.REFRESHED

    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    with mock.patch.object(queue, "execute", side_effect=execute):
        worker = asyncio.create_task(queue.run_worker())
        async with asyncio.timeout(WORKER_TIMEOUT):
            await drained.wait()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    assert executed == [7, 8]
    assert queue.pending == {}
    assert queue.in_flight == {}


async def test_the_worker_waits_instead_of_spinning_on_an_empty_queue(queue: RefreshQueue):
    """`run_worker` blocks on the submit signal, so an idle process costs nothing; a job
    submitted afterwards still wakes it."""
    executed = asyncio.Event()

    async def execute(_job: MeetingRefresh) -> JobOutcome:
        executed.set()
        return JobOutcome.REFRESHED

    with mock.patch.object(queue, "execute", side_effect=execute):
        worker = asyncio.create_task(queue.run_worker())
        await asyncio.sleep(0)
        assert not executed.is_set()

        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await executed.wait()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


async def test_a_job_that_outruns_its_timeout_fails_and_the_worker_takes_the_next_one(
    refresh_api: TelegramApi, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """The drain loop runs one job at a time, so a refresh that never returns would hold every
    other meeting's cards behind it forever. The timeout is what ends the hang by itself: the job
    is cancelled, counted as the failure it is, and the meetings behind it are drawn."""
    drained = asyncio.Event()

    async def execute(job: MeetingRefresh) -> JobOutcome:
        if job.meeting_id == 7:
            await asyncio.sleep(WORKER_TIMEOUT)
        drained.set()
        return JobOutcome.REFRESHED

    queue = RefreshQueue(refresh_api, metrics_client, report_interval=TICK_INTERVAL, job_timeout=JOB_TIMEOUT)
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))
    queue.submit(MeetingRefresh(meeting_id=8))

    with mock.patch.object(queue, "execute", side_effect=execute), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        async with asyncio.timeout(WORKER_TIMEOUT):
            await drained.wait()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    failures = [entry for entry in logs if entry["event"] == "Background job failed"]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "error"
    assert failures[0]["meeting_id"] == 7
    assert failures[0]["origin_update_id"] == 4242
    # The timeout is what the job died of, and the line has to say so: a cancelled render and a
    # render that raised are the same `failed` outcome and only the type tells them apart.
    assert failures[0]["error_type"] == "builtins.TimeoutError"
    assert queue.pending == {}
    assert queue.in_flight == {}
    # The timed-out job counts against the fault rate exactly like a raising one.
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)


async def test_the_reporter_keeps_publishing_while_a_job_is_stuck(queue: RefreshQueue, metrics_client: MetricsClient):
    """The whole point of the reporter being its own task: a job that hangs must not delay or
    silence the window that reports it. The age climbing on a series that never stops flowing is
    what a stuck queue looks like from outside, and an alarm can read a value where it cannot
    read an absence."""
    running = asyncio.Event()

    async def execute(_job: MeetingRefresh) -> JobOutcome:
        running.set()
        await asyncio.sleep(WORKER_TIMEOUT)
        return JobOutcome.REFRESHED

    # The job outlives every window this test watches, so what it proves is the reporter's
    # independence rather than the timeout that would eventually end the hang.
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))

    with mock.patch.object(queue, "execute", side_effect=execute), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
            await asyncio.sleep(TICK_INTERVAL * 4)
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    ages = emitted_values(metrics_client, MetricKey.OLDEST_JOB_AGE)
    assert len(ages) >= 2
    assert ages == sorted(ages)
    assert ages[-1] > ages[0]
    outstanding = [entry for entry in logs if entry["event"] == "Background job still outstanding"]
    assert len(outstanding) == len(ages)
    assert outstanding[0]["log_level"] == "info"
    assert outstanding[0]["meeting_id"] == 7
    assert outstanding[0]["origin_update_id"] == 4242
    # The job is being held, not waiting to be taken: only the state tells a wedged drain loop
    # from a backlog nobody has reached yet.
    assert outstanding[0]["state"] == "in_flight"
    assert outstanding[-1]["age_ms"] >= outstanding[0]["age_ms"]


# --- What the worker says it did ---


def test_a_submitted_refresh_reports_itself_scheduled(queue: RefreshQueue):
    """The line is what makes a card that never refreshed answerable: it says the change was
    accepted, and whether it became a job of its own or joined one already waiting. The update
    behind the submit is ambient wherever a submit happens, so the line does not repeat it."""
    with capture_logs() as logs:
        queue.submit(MeetingRefresh(meeting_id=7))
        queue.submit(MeetingRefresh(meeting_id=7))
        queue.submit(MeetingRefresh(meeting_id=8))

    scheduled = [entry for entry in logs if entry["event"] == "Background jobs scheduled"]
    assert [entry["log_level"] for entry in scheduled] == ["info"] * 3
    assert [entry["outcome"] for entry in scheduled] == ["queued", "coalesced", "queued"]
    assert [entry["meeting_id"] for entry in scheduled] == [7, 7, 8]
    assert [entry["pending"] for entry in scheduled] == [1, 1, 2]


async def test_a_finished_job_reports_the_refresh_and_the_update_behind_it(queue: RefreshQueue):
    """The worker runs under no update, so the job carries the one whose commit queued it —
    without it, a slow or failed refresh cannot be traced back to the interaction that caused
    it. The two latencies split a slow card from a backed-up queue."""
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))
    queue.submit(MeetingRefresh(meeting_id=7))

    with mock.patch.object(queue, "execute", return_value=JobOutcome.REFRESHED), capture_logs() as logs:
        await queue.run_next()

    finished = [entry for entry in logs if entry["event"] == "Background job finished"]
    assert len(finished) == 1
    assert finished[0]["log_level"] == "info"
    assert finished[0]["outcome"] == "refreshed"
    assert finished[0]["meeting_id"] == 7
    assert finished[0]["origin_update_id"] == 4242
    assert finished[0]["coalesced"] == 1
    assert finished[0]["attempt"] == 1
    assert finished[0]["queue_wait_ms"] >= 0
    assert finished[0]["duration_ms"] >= 0
    # Nothing was passed over, so naming a reason would read as a refresh that did not happen.
    assert "reason" not in finished[0]


async def test_a_job_whose_meeting_vanished_reports_the_skip(
    queue: RefreshQueue, mock_session: MockDbSession, registered_reconciler: None
):
    """A meeting deleted between submit and run leaves the job nothing to draw. That is a benign
    no-op rather than a lost refresh, and only the reason tells the two apart."""
    queue.submit(MeetingRefresh(meeting_id=7))

    with capture_logs() as logs:
        await queue.run_next()

    finished = [entry for entry in logs if entry["event"] == "Background job finished"]
    assert len(finished) == 1
    assert finished[0]["log_level"] == "info"
    assert finished[0]["outcome"] == "skipped"
    assert finished[0]["reason"] == "meeting_gone"


async def test_a_failing_job_reports_the_failure_with_the_update_behind_it(queue: RefreshQueue):
    """The refresh is lost — nothing re-queues it — so this line is the only record that the
    committed change never reached the cards, and it has to name both the meeting and the
    update that caused it."""
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))

    with mock.patch.object(queue, "execute", side_effect=RuntimeError("render blew up")), capture_logs() as logs:
        await queue.run_next()

    failures = [entry for entry in logs if entry["event"] == "Background job failed"]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "error"
    assert failures[0]["outcome"] == "failed"
    assert failures[0]["meeting_id"] == 7
    assert failures[0]["origin_update_id"] == 4242
    assert failures[0]["error_type"] == "builtins.RuntimeError"
    assert queue.in_flight == {}


def test_only_a_queue_with_work_left_reports_an_abandonment(queue: RefreshQueue):
    """The warning says committed changes died with the process, so an orderly stop with nothing
    waiting must not raise one. Both cases run under one capture: an absence asserted against a
    line that was never produced passes for the wrong reason."""
    queue.submit(MeetingRefresh(meeting_id=7))

    with capture_logs() as logs:
        queue.report_abandoned()
        queue.pending.clear()
        queue.report_abandoned()

    abandoned = [entry for entry in logs if entry["event"] == "Background jobs abandoned at shutdown"]
    assert len(abandoned) == 1
    assert abandoned[0]["log_level"] == "warning"
    assert abandoned[0]["abandoned"] == 1


async def test_a_cancelled_worker_reports_what_it_leaves_behind(queue: RefreshQueue, metrics: MetricAssertions):
    """Cancellation is how the process stops the worker, so the report has to survive it rather
    than sit on a return path a cancelled task never reaches. A job the drain could not finish
    counts among the ones left behind exactly like one it never started."""
    running = asyncio.Event()

    async def block(_job: MeetingRefresh) -> JobOutcome:
        running.set()
        await asyncio.sleep(WORKER_TIMEOUT)
        return JobOutcome.REFRESHED

    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    with mock.patch.object(queue, "execute", side_effect=block), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
        await card_refresh.stop_worker(worker)

    abandoned = [entry for entry in logs if entry["event"] == "Background jobs abandoned at shutdown"]
    assert len(abandoned) == 1
    assert abandoned[0]["abandoned"] == 2
    # No job reached an outcome, so none reports one: a deploy cancels every worker in the fleet at
    # once, and a sample per cancelled job would read as a burst of faults on the alarm the rolling
    # deploy is watching.
    metrics.assert_not_emitted(name=MetricKey.FAULT)


# --- Stopping the worker ---


async def test_the_shutdown_drain_finishes_the_jobs_already_queued(queue: RefreshQueue, metrics: MetricAssertions):
    """The queue lives in this process only, so a job still waiting when the worker stops is a
    committed change no card will ever show. Stopping therefore spends the deadline on them
    instead of dropping them where they stand, and the jobs it draws are counted and sampled like
    any other — on the window the drain itself closes, since the reporter died with the group."""
    executed: list[int] = []
    running = asyncio.Event()
    # Longer than the test, so the drain's publication is the only one and the counts it carries
    # are unambiguously the interrupted window's.
    queue.report_interval = WORKER_TIMEOUT

    async def execute(job: MeetingRefresh) -> JobOutcome:
        executed.append(job.meeting_id)
        if job.meeting_id == 7 and job.attempt == 1:
            running.set()
            await asyncio.sleep(WORKER_TIMEOUT)
        return JobOutcome.REFRESHED

    with mock.patch.object(queue, "execute", side_effect=execute), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
        # The drain loop is held inside meeting 7, so these two can only be reached by the stop.
        queue.submit(MeetingRefresh(meeting_id=8))
        queue.submit(MeetingRefresh(meeting_id=9))
        await card_refresh.stop_worker(worker)

    assert executed == [7, 8, 9, 7]
    assert queue.pending == {}
    # Nothing was left over, so the shutdown must not claim any change died with the process.
    assert [entry for entry in logs if entry["event"] == "Background jobs abandoned at shutdown"] == []
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=3)
    metrics.assert_emitted(name=MetricKey.JOBS_SUCCEEDED, value=3, times=1)


async def test_the_job_the_stop_interrupts_is_attempted_again_by_the_drain(queue: RefreshQueue):
    """Its cards were left half drawn or not drawn at all, so the change is still outstanding and
    the drain has to reach it. The attempt number is what tells the retry from a fresh submit on
    the line the second run writes."""
    attempts: list[int] = []
    running = asyncio.Event()

    async def execute(job: MeetingRefresh) -> JobOutcome:
        attempts.append(job.attempt)
        if job.attempt == 1:
            running.set()
            await asyncio.sleep(WORKER_TIMEOUT)
        return JobOutcome.REFRESHED

    with mock.patch.object(queue, "execute", side_effect=execute):
        worker = asyncio.create_task(queue.run_worker())
        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
        await card_refresh.stop_worker(worker)

    assert attempts == [1, 2]
    assert queue.pending == {}
    assert queue.in_flight == {}


async def test_the_shutdown_drain_gives_up_at_the_deadline(queue: RefreshQueue):
    """The orchestrator's kill timer is running while the drain spends its deadline, so a job that
    will not finish has to be given up on rather than take the teardown down with it."""
    running = asyncio.Event()

    async def block(_job: MeetingRefresh) -> JobOutcome:
        running.set()
        await asyncio.sleep(WORKER_TIMEOUT)
        return JobOutcome.REFRESHED

    with mock.patch.object(queue, "execute", side_effect=block), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
        started = perf_counter()
        await card_refresh.stop_worker(worker)

    # The wait is the deadline's, not the blocked job's, which would have run for far longer.
    assert perf_counter() - started < WORKER_TIMEOUT
    abandoned = [entry for entry in logs if entry["event"] == "Background jobs abandoned at shutdown"]
    assert len(abandoned) == 1
    assert abandoned[0]["abandoned"] == 1


async def test_a_stopping_queue_takes_no_more_work(queue: RefreshQueue):
    """Anything accepted during the drain could only lengthen what the shutdown abandons, and the
    refusal is a warning because a card the submitter counted on stays stale."""
    worker = asyncio.create_task(queue.run_worker())
    await asyncio.sleep(0)
    await card_refresh.stop_worker(worker)

    with capture_logs() as logs:
        accepted = queue.submit(MeetingRefresh(meeting_id=7))

    assert accepted is False
    assert queue.pending == {}
    dropped = [entry for entry in logs if entry["event"] == "Meeting card refresh dropped"]
    assert len(dropped) == 1
    assert dropped[0]["log_level"] == "warning"
    assert dropped[0]["reason"] == "shutting_down"
    assert dropped[0]["meeting_id"] == 7


async def test_the_drain_publishes_the_window_the_stop_interrupted(queue: RefreshQueue, metrics: MetricAssertions):
    """The reporter is cancelled with the group, so the window it was going to close would
    otherwise take its accounting with it: the jobs it ran counted nowhere and every series ending
    on a stale sample right when the process went away."""
    running = asyncio.Event()
    # Longer than the test, so the drain's publication is the only one and the counts it carries
    # are unambiguously the interrupted window's.
    queue.report_interval = WORKER_TIMEOUT

    async def execute(_job: MeetingRefresh) -> JobOutcome:
        running.set()
        return JobOutcome.REFRESHED

    with mock.patch.object(queue, "execute", side_effect=execute), capture_logs(processors=[merge_contextvars]) as logs:
        worker = asyncio.create_task(queue.run_worker())
        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await running.wait()
        await card_refresh.stop_worker(worker)

    drains = [entry for entry in logs if entry["event"] == "Background job drain finished"]
    assert len(drains) == 1
    assert drains[0]["succeeded"] == 1
    metrics.assert_emitted(name=MetricKey.JOBS_SUCCEEDED, value=1, properties={"run_id": drains[0]["run_id"]})


async def test_the_worker_names_the_database_sessions_it_holds(queue: RefreshQueue):
    """Sessions are counted per connection context, so without one of its own everything the worker
    holds falls into the unknown bucket, where a session it never returned cannot be told from
    anyone else's."""
    with mock.patch.object(card_refresh.db, "set_connection_context") as set_context:
        worker = asyncio.create_task(queue.run_worker())
        await asyncio.sleep(0)
        await card_refresh.stop_worker(worker)

    set_context.assert_called_once_with("BackgroundJobs")


# --- The reporting window ---


async def test_an_idle_window_still_publishes_its_zeros(queue: RefreshQueue, metrics: MetricAssertions):
    """Every series the worker owns is read as a value, so each has to have one on every window:
    a gauge that only reports when there is something to say cannot be alarmed on, and a widget
    charting it would draw a rate out of whichever windows happened to be busy."""
    with capture_logs() as logs:
        await queue.publish()

    metrics.assert_emitted(name=MetricKey.JOBS_QUEUED, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.JOBS_SUCCEEDED, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.JOBS_FAILED, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.OLDEST_JOB_AGE, value=0, unit=MetricUnit.MILLISECONDS, times=1)
    # A synthetic zero here would drag the p90 down and hide a worker whose jobs are slowing.
    metrics.assert_not_emitted(name=MetricKey.JOB_PROCESSING_TIME)
    assert len([entry for entry in logs if entry["event"] == "Background job drain finished"]) == 1
    # Naming an oldest job on an empty queue would put a meeting id behind every one of those zeros.
    assert not [entry for entry in logs if entry["event"] == "Background job still outstanding"]


async def test_a_window_publishes_what_it_ran_and_starts_the_next_one_from_zero(
    queue: RefreshQueue, metrics: MetricAssertions
):
    """Every count belongs to one window, so the widget reads a rate rather than a total that only
    ever grows. A job that raised is counted once, as the unit of work that failed."""

    async def execute(job: MeetingRefresh) -> JobOutcome:
        if job.meeting_id == 8:
            raise RuntimeError("render blew up")
        return JobOutcome.REFRESHED

    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    with capture_logs() as logs, mock.patch.object(queue, "execute", side_effect=execute):
        await queue.run_next()
        await queue.run_next()
        await queue.publish()
        await queue.publish()

    metrics.assert_emitted(name=MetricKey.JOBS_QUEUED, value=2, times=1)
    metrics.assert_emitted(name=MetricKey.JOBS_SUCCEEDED, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.JOBS_FAILED, value=1, times=1)
    metrics.assert_emitted(name=MetricKey.JOB_PROCESSING_TIME, unit=MetricUnit.MILLISECONDS, times=2)
    # The second window ran nothing, and reports so rather than repeating the first one's work.
    metrics.assert_emitted(name=MetricKey.JOBS_SUCCEEDED, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.JOBS_FAILED, value=0, times=1)
    drains = [entry for entry in logs if entry["event"] == "Background job drain finished"]
    assert [(entry["succeeded"], entry["failed"], entry["queued_high_water"]) for entry in drains] == [
        (1, 1, 2),
        (0, 0, 0),
    ]


async def test_every_job_reports_one_fault_sample_whichever_way_it_ended(
    queue: RefreshQueue, metrics: MetricAssertions
):
    """A background job is a unit of work like a handler invocation, and the fault rate is a rate:
    the successes have to be sampled too, or a worker that fails one job in a quiet hour reads as
    a process failing everything it did."""

    async def execute(job: MeetingRefresh) -> JobOutcome:
        if job.meeting_id == 8:
            raise RuntimeError("render blew up")
        return JobOutcome.REFRESHED

    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    with mock.patch.object(queue, "execute", side_effect=execute):
        await queue.run_next()
        await queue.run_next()

    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1)


def test_a_wedged_worker_keeps_reporting_the_jobs_waiting_behind_it(queue: RefreshQueue, metrics: MetricAssertions):
    """Depth is reported from what is standing at publication time as well as from the submits,
    so a queue nobody adds to and nobody drains still reads as a backlog instead of a zero."""
    queue.pending[7] = MeetingRefresh(meeting_id=7)
    queue.pending[8] = MeetingRefresh(meeting_id=8)

    queue.report()

    metrics.assert_emitted(name=MetricKey.JOBS_QUEUED, value=2, times=1)


async def test_the_oldest_job_age_climbs_for_a_queue_nobody_is_draining(
    queue: RefreshQueue, metrics_client: MetricsClient
):
    """The drain loop dying is the failure the depth alone cannot report: a backlog that stopped
    moving looks exactly like a backlog that is being worked through. The age is what separates
    them, and it has to be measured from the submit for a job nobody has taken yet."""
    queue.submit(MeetingRefresh(meeting_id=7, origin_update_id=4242))

    with capture_logs() as logs:
        await queue.publish()
        await asyncio.sleep(TICK_INTERVAL)
        await queue.publish()

    ages = emitted_values(metrics_client, MetricKey.OLDEST_JOB_AGE)
    assert len(ages) == 2
    assert ages[1] > ages[0]
    outstanding = [entry for entry in logs if entry["event"] == "Background job still outstanding"]
    assert len(outstanding) == 2
    assert outstanding[0]["state"] == "pending"
    assert outstanding[0]["meeting_id"] == 7
    assert outstanding[0]["origin_update_id"] == 4242
    assert outstanding[1]["age_ms"] > outstanding[0]["age_ms"]


async def test_the_records_of_a_window_carry_the_run_id_its_jobs_are_bound_to(
    queue: RefreshQueue, metrics: MetricAssertions
):
    """This is the pivot the metric plane exists for: an alarm on a count leads to a `run_id`, and
    that id has to select the lines of the jobs the window covered. The property rides each emit
    because the client outlives the window — a global property would pin one window's id on every
    record the process wrote afterwards — and the reporter is its only writer, because a property
    is last-writer-wins across the whole flush window."""
    queue.submit(MeetingRefresh(meeting_id=7))

    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch.object(queue, "execute", return_value=JobOutcome.REFRESHED):
            await queue.run_next()
        await queue.publish()
        await queue.publish()

    drains = [entry for entry in logs if entry["event"] == "Background job drain finished"]
    run_ids = [entry["run_id"] for entry in drains]
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]
    for run_id in run_ids:
        assert len(run_id) == 32  # uuid4().hex
        metrics.assert_emitted(name=MetricKey.JOBS_QUEUED, properties={"run_id": run_id}, times=1)
    # The job ran under the window that publishes it, so its line is what that window's id selects.
    finished = [entry for entry in logs if entry["event"] == "Background job finished"]
    assert [entry["run_id"] for entry in finished] == [run_ids[0]]
    # Nothing the job itself emits carries an id of its own: one window's flush covers every job it
    # ran, so a per-job property would be reported as describing all of them.
    metrics.assert_emitted(name=MetricKey.FAULT, properties={}, properties_exact=True, times=1)
    metrics.assert_emitted(name=MetricKey.JOB_PROCESSING_TIME, properties={}, properties_exact=True, times=1)


# --- The process-level queue ---


def test_configure_publishes_the_queue_to_current_queue(refresh_api: TelegramApi, metrics_client: MetricsClient):
    assert card_refresh.current_queue() is None

    configured = card_refresh.configure(refresh_api, metrics_client, max_pending=3)

    assert card_refresh.current_queue() is configured
    assert configured.api is refresh_api
    assert configured.metrics is metrics_client
    assert configured.max_pending == 3


def test_configure_worker_gives_the_queue_an_api_and_a_client_of_its_own(
    bot: mock.AsyncMock, metrics_client: MetricsClient
):
    """Capture mode is per-instance state on the api and the client is flushed at the end of every
    window, so a worker sharing either with a handler or a recurrent event would collide with it."""
    configured = card_refresh.configure_worker(cast(ExtBot, bot), WorkerLimits(job_timeout=7.5, drain_deadline=3.5))

    assert card_refresh.current_queue() is configured
    assert configured.metrics is not metrics_client
    assert configured.job_timeout == 7.5
    assert configured.drain_deadline == 3.5
    # The queue takes the card text off its own lines, which it may only do to an api nobody shares.
    assert configured.api.log_card_text is False
    # And that api is handed no queue, which is what keeps a job's fan-out off the queue running it.
    assert configured.api.refresh_queue is None


def test_worker_limits_come_from_the_deployed_configuration():
    """Both bounds are what a teardown has to fit inside, so a process must run the deployed values
    rather than the library defaults it would silently fall back to."""
    app_config = AppConfig(run_mode=RunModes.POLLING, background_job_timeout_seconds=42.0, background_drain_seconds=4.0)

    limits = WorkerLimits.from_config(app_config)

    assert limits == WorkerLimits(job_timeout=42.0, drain_deadline=4.0)


def test_current_queue_is_none_where_no_runtime_configured_one():
    """A CLI job and a test render their cards inline; a submit there has to be a no-op rather
    than a failure, which is what the None answer buys the enqueuing call sites."""
    assert card_refresh.current_queue() is None
