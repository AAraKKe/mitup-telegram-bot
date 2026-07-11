import inspect
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from pydantic import BaseModel
from sqlalchemy import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.error import BadRequest, Forbidden, TimedOut
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.config import DbConfig
from mitup_bot.models import Meetup, MeetupLocation
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.protocols import ContextOrBotAdapter
from tests.helpers import make_test_metrics_client
from tests.helpers.db_errors import integrity_error
from tests.helpers.fixtures import create_meetup, create_message, create_user
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


@pytest.fixture(autouse=True, scope="function")
def reset_db():
    # Make sure to reset the db configuration after each test so we can
    # validate its behavior
    yield
    db.__sessionmaker = None
    db.__pool_metrics = None


@pytest.fixture(
    params=(
        MeetupLocation(name="Test", coordinates=(123.1, 321.1)),
        MeetupLocation(name="Test"),
        MeetupLocation(coordinates=(123.1, 321.1)),
    ),
    ids=("full_location", "only_name_location", "only_coordinates_location"),
)
def serializable_model(request: pytest.FixtureRequest):
    return request.param


def test_build_db_url_assembles_dsn_from_plain_fields(db_config: DbConfig):
    assert db.build_db_url(db_config) == URL.create(
        drivername="postgresql+psycopg",
        username="user",
        password="password",
        host="testhost",
        port=5432,
        database="db",
    )


def test_db_initilization(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.async_sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_async_engine") as mock_engine,
    ):
        db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(
        db.build_db_url(db_config),
        echo=db_config.engine_echo,
        json_serializer=db.serialize_pydantic_model,
        json_deserializer=db.deserialize_pydantic_model,
        pool_size=db_config.pool_size,
        max_overflow=db_config.max_overflow,
        pool_timeout=db_config.pool_timeout,
    )


def test_db_cannot_be_configured_twice(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.async_sessionmaker") as mock_maker,
        mock.patch("mitup_bot.db.create_async_engine") as mock_engine,
    ):
        db.configure_db(db_config)

        with pytest.raises(db.DbAlreadyInitializedError):
            db.configure_db(db_config)

    mock_maker.assert_called_once()
    mock_engine.assert_called_once_with(
        db.build_db_url(db_config),
        echo=db_config.engine_echo,
        json_serializer=db.serialize_pydantic_model,
        json_deserializer=db.deserialize_pydantic_model,
        pool_size=db_config.pool_size,
        max_overflow=db_config.max_overflow,
        pool_timeout=db_config.pool_timeout,
    )


def test_configure_db_with_metrics_client_instruments_pool(db_config: DbConfig):
    pool_metrics_client = make_test_metrics_client()

    with (
        mock.patch("mitup_bot.db.async_sessionmaker"),
        mock.patch("mitup_bot.db.create_async_engine") as mock_engine,
        mock.patch("mitup_bot.db.instrument_pool") as mock_instrument,
    ):
        db.configure_db(db_config, metrics_client=pool_metrics_client)

    mock_instrument.assert_called_once_with(mock_engine.return_value, pool_metrics_client)


def test_configure_db_without_metrics_client_skips_instrumentation(db_config: DbConfig):
    with (
        mock.patch("mitup_bot.db.async_sessionmaker"),
        mock.patch("mitup_bot.db.create_async_engine"),
        mock.patch("mitup_bot.db.instrument_pool") as mock_instrument,
    ):
        db.configure_db(db_config)

    mock_instrument.assert_not_called()


async def test_begin_emits_checkout_wait_time(mock_session: MockDbSession):
    pool_metrics_client = make_test_metrics_client()
    db.__pool_metrics = pool_metrics_client

    async with db.begin():
        ...

    MetricAssertions(pool_metrics_client).assert_emitted(
        name=MetricKey.DB_POOL_CHECKOUT_WAIT_TIME, value=None, unit=MetricUnit.MILLISECONDS, times=1
    )


async def test_begin_emits_pool_timeout_and_reraises(mock_session: MockDbSession):
    pool_metrics_client = make_test_metrics_client()
    db.__pool_metrics = pool_metrics_client
    mock_session.connection = mock.AsyncMock(side_effect=PoolTimeoutError("QueuePool limit reached"))

    with pytest.raises(PoolTimeoutError):
        async with db.begin():
            raise AssertionError("the transaction body must not run without a connection")

    pool_assertions = MetricAssertions(pool_metrics_client)
    pool_assertions.assert_emitted(name=MetricKey.DB_POOL_TIMEOUT, value=1, times=1)
    # A timed-out checkout never acquired a connection, so no wait time is recorded for it.
    pool_assertions.assert_not_emitted(name=MetricKey.DB_POOL_CHECKOUT_WAIT_TIME)


async def test_begin_without_pool_metrics_stays_silent(mock_session: MockDbSession):
    async with db.begin():
        ...

    # No metrics client was configured: the eager connection acquisition must not blow up.
    mock_session.connection.assert_awaited_once()


async def test_cannot_get_transaction_without_configuring_db():
    with pytest.raises(db.DbNotInitializedError):
        async with db.begin():
            pass


async def test_session_decorator(mock_session: MockDbSession):
    async def f(s: AsyncSession) -> int:
        return 1

    wrapped = db.with_session(f)()

    assert inspect.iscoroutine(wrapped)

    assert await wrapped == 1


def test_engine_json_serializer(meeting: Meetup):
    serialized = db.serialize_pydantic_model(meeting)

    assert serialized == meeting.model_dump_json()


def test_engine_json_deserializer(serializable_model: BaseModel):
    deserialized = db.deserialize_pydantic_model(serializable_model.model_dump_json())

    assert deserialized == serializable_model


def test_json_deserializer_with_non_serializable_model():
    assert db.deserialize_pydantic_model('{"something": "Test"}') is None


async def test_racy_flush_returns_built_value_when_flush_succeeds(mock_session: MockDbSession):
    built = mock.MagicMock()

    assert await db.racy_flush(mock_session, lambda: built, constraint=JOINED_USERS_UNIQUE_CONSTRAINT) is built
    mock_session.assert_flushed()


async def test_racy_flush_swallows_named_constraint_clash(mock_session: MockDbSession):
    mock_session.flush.side_effect = integrity_error(JOINED_USERS_UNIQUE_CONSTRAINT)

    # The named uniqueness violation is the expected concurrent-writer clash: reported as None.
    assert await db.racy_flush(mock_session, mock.MagicMock, constraint=JOINED_USERS_UNIQUE_CONSTRAINT) is None


@pytest.mark.parametrize(
    "constraint_name",
    # An orig without diagnostics, and one naming a different constraint (e.g. a foreign key).
    [None, "joined_users_meetup_id_fkey"],
    ids=["no_diag", "different_constraint"],
)
async def test_racy_flush_reraises_other_integrity_errors(mock_session: MockDbSession, constraint_name: str | None):
    mock_session.flush.side_effect = integrity_error(constraint_name)

    # Only the named constraint clash is a no-op; every other IntegrityError must surface.
    with pytest.raises(IntegrityError):
        await db.racy_flush(mock_session, mock.MagicMock, constraint=JOINED_USERS_UNIQUE_CONSTRAINT)


# ---------------------------------------------------------------------------
# with_session(write=True): the two-phase handler lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture
def fanout_bot() -> mock.AsyncMock:
    return mock.AsyncMock(spec=ExtBot)


@pytest.fixture
def fanout_metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def fanout_metrics(fanout_metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(fanout_metrics_client)


@pytest.fixture
def write_context(fanout_bot: mock.AsyncMock, fanout_metrics_client: MetricsClient) -> SimpleNamespace:
    """Stand-in for MitupContext: the decorator only needs an argument exposing `.api`."""
    api = TelegramApi()
    api.adapter = cast(ContextOrBotAdapter, BotAdapter(cast(ExtBot, fanout_bot), fanout_metrics_client))
    return SimpleNamespace(api=api)


async def test_write_mode_commits_before_any_queued_call_runs(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    events: list[str] = []
    # The transaction context manager's __aexit__ is db.begin()'s commit point.
    mock_session.begin.return_value.__aexit__.side_effect = lambda *exc_info: events.append("commit")
    fanout_bot.send_message.side_effect = lambda **kwargs: events.append("bot-send")
    user = create_user(id=1, tg_user_id=100)

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.send_message_to_user(user, "you joined")
        events.append("handler-returned")

    await handler(write_context)

    assert events == ["handler-returned", "commit", "bot-send"]


async def test_write_mode_handler_exception_discards_queue(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    user = create_user(id=1, tg_user_id=100)

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.send_message_to_user(user, "never delivered")
        raise RuntimeError("handler blew up")

    with pytest.raises(RuntimeError, match="handler blew up"):
        await handler(write_context)

    # Nothing about the rolled-back state was rendered to anyone...
    fanout_bot.send_message.assert_not_called()
    assert mock_session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError
    # ...and capture mode was torn down: the next write handler can start its own.
    write_context.api.begin_capture()


async def test_write_mode_immediate_failure_aborts_transaction_and_queue(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    user = create_user(id=1, tg_user_id=100)
    fanout_bot.send_message.side_effect = RuntimeError("telegram down")

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.send_message_to_user(user, "queued, then dropped")
        await context.api.immediate.send_message_to_user(user, "in-transaction")

    with pytest.raises(RuntimeError, match="telegram down"):
        await handler(write_context)

    # Only the immediate call reached the bot; its in-transaction failure rolled the
    # transaction back and the queued call was discarded with it.
    fanout_bot.send_message.assert_awaited_once()
    assert mock_session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError


async def test_write_mode_requires_a_context_like_argument():
    @db.with_session(write=True)
    async def not_a_handler(session: AsyncSession, payload: object):
        raise AssertionError("must not run")

    # The last positional argument is not api-bearing: the TypeError fires BEFORE any session
    # opens — the db is left unconfigured here, so getting past the check would raise
    # DbNotInitializedError instead.
    with pytest.raises(TypeError, match="requires the MitupContext"):
        await not_a_handler(object())


# ---------------------------------------------------------------------------
# begin_write: the lifecycle primitive, driven directly (the CLI batch-job form)
# ---------------------------------------------------------------------------


async def test_begin_write_commits_before_queued_calls_drain(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    """Non-handler code (CLI batch jobs) has no MitupContext: it drives ``db.begin_write``
    directly, one critical section per ``async with`` block, and gets the same
    commit-before-fanout ordering the write-mode decorator provides."""
    events: list[str] = []
    mock_session.begin.return_value.__aexit__.side_effect = lambda *exc_info: events.append("commit")
    fanout_bot.send_message.side_effect = lambda **kwargs: events.append("bot-send")
    user = create_user(id=1, tg_user_id=100)
    api: TelegramApi = write_context.api

    async with db.begin_write(api):
        await api.send_message_to_user(user, "swept")
        events.append("body-finished")

    assert events == ["body-finished", "commit", "bot-send"]


async def test_begin_write_body_exception_discards_queue(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    user = create_user(id=1, tg_user_id=100)
    api: TelegramApi = write_context.api

    with pytest.raises(RuntimeError, match="sweep blew up"):
        async with db.begin_write(api):
            await api.send_message_to_user(user, "never delivered")
            raise RuntimeError("sweep blew up")

    # Nothing about the rolled-back state was rendered to anyone...
    fanout_bot.send_message.assert_not_called()
    assert mock_session.begin.return_value.__aexit__.await_args.args[0] is RuntimeError
    # ...and capture mode was torn down: the next critical section can start its own.
    api.begin_capture()


@pytest.mark.parametrize(
    "status, expected_status, metric_times",
    [
        (UserStatus.MEMBER, UserStatus.LEFT, 1),
        # mark_inactive is a no-op for JOINED_ONLY: no transition, and the counter only
        # reflects genuine MEMBER → LEFT departures.
        (UserStatus.JOINED_ONLY, UserStatus.JOINED_ONLY, 0),
    ],
    ids=["member_marked_left", "joined_only_untouched"],
)
async def test_write_mode_reconcile_marks_unreachable_user(
    mock_session: MockDbSession,
    write_context: SimpleNamespace,
    fanout_bot: mock.AsyncMock,
    fanout_metrics: MetricAssertions,
    fanout_metrics_client: MetricsClient,
    status: UserStatus,
    expected_status: UserStatus,
    metric_times: int,
):
    user = create_user(id=1, tg_user_id=555, status=status)
    mock_session.add_user(user)
    fanout_bot.send_message.side_effect = Forbidden("Forbidden: bot was blocked by the user")

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.send_message_to_user(user, "unreachable")

    await handler(write_context)
    await fanout_metrics_client.flush()

    assert user.status is expected_status
    fanout_metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, times=metric_times)


async def test_write_mode_reconcile_dedups_repeated_inactive_user(
    mock_session: MockDbSession,
    write_context: SimpleNamespace,
    fanout_bot: mock.AsyncMock,
    fanout_metrics: MetricAssertions,
    fanout_metrics_client: MetricsClient,
):
    user = create_user(id=1, tg_user_id=555)
    mock_session.add_user(user)
    fanout_bot.send_message.side_effect = Forbidden("Forbidden: bot was blocked by the user")

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        # Both queued sends target the same user and both fail with Forbidden at drain time.
        await context.api.send_message_to_user(user, "one")
        await context.api.send_message_to_user(user, "two")

    await handler(write_context)
    await fanout_metrics_client.flush()

    assert user.status is UserStatus.LEFT
    # The reconcile deduplicates the recorded ids: one lookup, one transition, one metric.
    assert sum("tg_user_id = 555" in query for query in mock_session.queries_executed) == 1
    fanout_metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, times=1)


async def test_write_mode_reconcile_deletes_messages_reported_gone(
    mock_session: MockDbSession, write_context: SimpleNamespace, fanout_bot: mock.AsyncMock
):
    meeting = create_meetup(id=10, title="Meeting", language="en")
    create_user(id=1, tg_user_id=100, owned_meetings=[meeting])
    msg = create_message(id=7, inline_message_id=None, chat_id=100, message_id=501, meetup_id=10)
    fanout_bot.edit_message_text.side_effect = BadRequest("Message to edit not found")

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.update_meeting_messages(meeting=meeting, current_message=msg)

    await handler(write_context)

    delete_queries = [query for query in mock_session.queries_executed if query.startswith("DELETE FROM messages")]
    assert len(delete_queries) == 1
    assert "messages.id IN (7)" in delete_queries[0]


async def test_write_mode_connectivity_failure_propagates_after_reconcile(
    mock_session: MockDbSession,
    write_context: SimpleNamespace,
    fanout_bot: mock.AsyncMock,
    fanout_metrics: MetricAssertions,
    fanout_metrics_client: MetricsClient,
):
    blocked = create_user(id=1, tg_user_id=555)
    healthy = create_user(id=2, tg_user_id=556)
    mock_session.add_user(blocked)
    fanout_bot.send_message.side_effect = [Forbidden("Forbidden: bot was blocked by the user"), TimedOut()]

    @db.with_session(write=True)
    async def handler(session: AsyncSession, context: SimpleNamespace):
        await context.api.send_message_to_user(blocked, "one")
        await context.api.send_message_to_user(healthy, "two")
        await context.api.send_message_to_user(healthy, "three")

    with pytest.raises(TimedOut):
        await handler(write_context)
    await fanout_metrics_client.flush()

    # The drain stopped at the connectivity failure (the third call never ran)...
    assert fanout_bot.send_message.await_count == 2
    # ...but the fix-ups collected before it still landed in the reconcile transaction.
    assert blocked.status is UserStatus.LEFT
    fanout_metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, times=1)
