import asyncio
from collections.abc import Generator
from contextlib import suppress
from typing import cast
from unittest import mock

import pytest
from structlog.testing import capture_logs
from telegram.error import BadRequest
from telegram.ext import ExtBot

from mitup_bot import card_refresh, reconcile
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.card_refresh import MeetingRefresh, RefreshQueue
from mitup_bot.models import Meetup
from mitup_bot.protocols import ContextOrBotAdapter
from tests.helpers import make_test_metrics_client
from tests.helpers.fixtures import create_meetup, create_message, create_user
from tests.helpers.stub_db import MockDbSession

WORKER_TIMEOUT = 5.0


@pytest.fixture
def bot() -> mock.AsyncMock:
    return mock.AsyncMock(spec=ExtBot)


@pytest.fixture
def refresh_api(bot: mock.AsyncMock) -> TelegramApi:
    """The api the queue owns outright: the worker re-enters capture mode on it, and capture
    mode is per-instance, so it can never be one a handler is also using."""
    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, BotAdapter(bot=cast(ExtBot, bot), metrics=make_test_metrics_client()))
    return api


@pytest.fixture
def queue(refresh_api: TelegramApi) -> RefreshQueue:
    return RefreshQueue(refresh_api)


@pytest.fixture(autouse=True)
def process_queue_isolation() -> Generator[None]:
    yield
    card_refresh.__queue = None


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

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=merged_skip)


def test_a_skip_is_not_honoured_while_the_meeting_is_in_flight(queue: RefreshQueue):
    """The running job read the meeting before this change committed, so it may still lay a
    stale render over the very card the submitter drew: that card needs refreshing too."""
    queue.in_flight.add(7)

    assert queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=None)


def test_an_in_flight_meeting_drops_the_skip_before_coalescing(queue: RefreshQueue):
    """Order matters: dropping the skip first is what stops two submits that agree on a card
    from agreeing their way past the in-flight rule."""
    queue.in_flight.add(7)
    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    queue.submit(MeetingRefresh(meeting_id=7, skip_message_db_id=41))

    assert queue.pending[7] == MeetingRefresh(meeting_id=7, skip_message_db_id=None)


# --- The pending cap ---


def test_the_pending_cap_drops_a_new_meeting_and_reports_it(refresh_api: TelegramApi):
    """Submits come from post-commit code that must not block, so the cap absorbs a wedged
    worker — but silently losing a committed change is exactly what the line has to prevent."""
    queue = RefreshQueue(refresh_api, max_pending=2)
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


def test_the_pending_cap_still_coalesces_onto_a_waiting_meeting(refresh_api: TelegramApi):
    """The cap bounds distinct meetings; refusing a merge would discard a committed change that
    costs no extra entry."""
    queue = RefreshQueue(refresh_api, max_pending=2)
    queue.submit(MeetingRefresh(meeting_id=1, skip_message_db_id=41))
    queue.submit(MeetingRefresh(meeting_id=2))

    assert queue.submit(MeetingRefresh(meeting_id=1, skip_message_db_id=42))

    assert queue.pending[1] == MeetingRefresh(meeting_id=1, skip_message_db_id=None)


# --- Taking a job ---


def test_take_claims_jobs_oldest_first_and_marks_them_in_flight(queue: RefreshQueue):
    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    assert queue.take() == MeetingRefresh(meeting_id=7)

    assert queue.in_flight == {7}
    assert list(queue.pending) == [8]
    assert queue.take() == MeetingRefresh(meeting_id=8)
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
    assert queue.in_flight == set()


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
    await queue.execute(MeetingRefresh(meeting_id=7))

    bot.edit_message_text.assert_not_awaited()


# --- The worker ---


async def test_run_next_does_nothing_when_the_queue_is_empty(queue: RefreshQueue, bot: mock.AsyncMock):
    """The worker re-checks the queue after every wake, so an empty take has to be a clean
    no-op rather than a job that is not there."""
    await queue.run_next()

    bot.edit_message_text.assert_not_awaited()
    assert queue.in_flight == set()


async def test_the_worker_drains_the_queue_and_survives_a_failing_job(queue: RefreshQueue):
    """One meeting's failure must not take the worker with it: every other meeting's cards are
    waiting on the same single drain."""
    drained = asyncio.Event()
    executed: list[int] = []

    async def execute(job: MeetingRefresh):
        executed.append(job.meeting_id)
        if job.meeting_id == 7:
            raise RuntimeError("render blew up")
        drained.set()

    queue.submit(MeetingRefresh(meeting_id=7))
    queue.submit(MeetingRefresh(meeting_id=8))

    with mock.patch.object(queue, "execute", side_effect=execute), capture_logs() as logs:
        worker = asyncio.create_task(queue.run_worker())
        async with asyncio.timeout(WORKER_TIMEOUT):
            await drained.wait()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    assert executed == [7, 8]
    assert queue.pending == {}
    assert queue.in_flight == set()
    failures = [entry for entry in logs if entry["event"] == "Meeting card refresh failed"]
    assert len(failures) == 1
    assert failures[0]["log_level"] == "error"
    assert failures[0]["meeting_id"] == 7
    assert failures[0]["error_type"] == "builtins.RuntimeError"


async def test_the_worker_waits_instead_of_spinning_on_an_empty_queue(queue: RefreshQueue):
    """`run_worker` blocks on the submit signal, so an idle process costs nothing; a job
    submitted afterwards still wakes it."""
    executed = asyncio.Event()

    with mock.patch.object(queue, "execute", side_effect=lambda job: executed.set()):
        worker = asyncio.create_task(queue.run_worker())
        await asyncio.sleep(0)
        assert not executed.is_set()

        queue.submit(MeetingRefresh(meeting_id=7))
        async with asyncio.timeout(WORKER_TIMEOUT):
            await executed.wait()
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


# --- The process-level queue ---


def test_configure_publishes_the_queue_to_current_queue(refresh_api: TelegramApi):
    assert card_refresh.current_queue() is None

    configured = card_refresh.configure(refresh_api, max_pending=3)

    assert card_refresh.current_queue() is configured
    assert configured.api is refresh_api
    assert configured.max_pending == 3


def test_current_queue_is_none_where_no_runtime_configured_one():
    """A CLI job and a test render their cards inline; a submit there has to be a no-op rather
    than a failure, which is what the None answer buys the enqueuing call sites."""
    assert card_refresh.current_queue() is None
