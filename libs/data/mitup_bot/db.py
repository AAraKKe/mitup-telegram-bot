from __future__ import annotations

import functools
import time
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, Concatenate, Literal, Protocol, cast, overload

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import URL, Engine, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry, PoolProxiedConnection, QueuePool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.config import DbConfig
from mitup_bot.models import MeetupLocation, MessageButtons
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit, current_metrics_client

if TYPE_CHECKING:  # pragma: no cover
    # ContextOrBotAdapter reaches python-telegram-bot via mitup_bot.protocols; keeping the import
    # type-only is what lets this persistence layer stay PTB-free (the migrations Lambda loads it
    # without python-telegram-bot installed).
    from mitup_bot.protocols import ContextOrBotAdapter

log = structlog.get_logger(__name__)


class WritePhase(StrEnum):
    """Where in a write-mode critical section execution stands.

    The distinction the failure plane is built on: a `BODY` failure rolled the transaction back
    and the user's action did not happen, while a `POST_COMMIT_FAN_OUT` failure lost a render of
    a write that landed.
    """

    BODY = auto()
    POST_COMMIT_FAN_OUT = auto()


@dataclass(frozen=True)
class WriteState:
    phase: WritePhase
    committed: bool


# Left set when a critical section leaves through an exception, so the error handler — which runs
# long after `begin_write` has returned control — can still name the phase the failure came from.
WRITE_STATE: ContextVar[WriteState | None] = ContextVar("write_state", default=None)


def current_write_state() -> WriteState | None:
    """The write phase of the innermost critical section still accountable for this task."""
    return WRITE_STATE.get()


def clear_write_state():
    """Forget the phase mark at the boundary of the work that could have set it.

    `begin_write` leaves the mark behind on its failure path, since the error handler reads it
    long after the critical section returned control. Bounding that to the piece of work it
    describes is the caller's job: whoever owns the boundary clears it before the next one.
    """
    WRITE_STATE.set(None)


class OutboxProtocol(Protocol):
    """The reconcile surface of an api outbox: the DB fix-ups recorded while the queued
    fan-out drained, to be applied by the registered reconciler."""

    dead_message_ids: list[int]
    inactive_tg_user_ids: list[int]
    confirmed_render_digests: dict[int, str]


class WriteApi[OutboxT: OutboxProtocol](Protocol):
    """The capture/drain surface the write lifecycle drives on the api, expressed
    structurally so this module never depends on the api implementation."""

    @property
    def adapter(self) -> ContextOrBotAdapter: ...
    def begin_capture(self) -> OutboxT: ...
    def end_capture(self): ...
    async def execute_queued(self, outbox: OutboxT): ...


type OutboxReconciler = Callable[[AsyncSession, ContextOrBotAdapter, OutboxProtocol], Coroutine[Any, Any, None]]

__sessionmaker: async_sessionmaker[AsyncSession] | None = None
__connection_context: ContextVar[str] = ContextVar("connection_context", default="unknown")
__active_connections: Counter[str] = Counter()
__pool_metrics: MetricsClient | None = None
__outbox_reconciler: OutboxReconciler | None = None


class DbNotInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has not been initialized. See mitup_bot.db.configure_db for information.")


class DbAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has already been configured.")


class OutboxReconcilerNotRegisteredError(RuntimeError):
    def __init__(self):
        super().__init__(
            "No outbox reconciler has been registered. See mitup_bot.db.set_outbox_reconciler for information."
        )


def serialize_pydantic_model(model: BaseModel) -> str:
    return model.model_dump_json()


def deserialize_pydantic_model(data: str) -> BaseModel | None:
    # Try deserializing with each model until one works.
    # This is a pretty ugly solution but the deserialization seems to only be possible at an engine level
    # and we need to know the model to deserialize it.
    # We would need to keep adding more of these if we add more models with JSON fields.
    with suppress(ValidationError):
        return MeetupLocation.model_validate_json(data)
    with suppress(ValidationError):
        return MessageButtons.model_validate_json(data)
    return None


def set_outbox_reconciler(reconciler: OutboxReconciler):
    """Register the model-aware reconcile behavior the write lifecycle applies after a drain.

    The db layer owns the lifecycle ordering (capture, commit, drain, reconcile) but not the
    models the fix-ups touch; every process entry point that runs write-mode critical
    sections registers the reconciler once at startup (see mitup_bot.reconcile).
    """
    global __outbox_reconciler
    __outbox_reconciler = reconciler


def set_connection_context(context: str):
    __connection_context.set(context)


def get_open_connections(context: str) -> int:
    """Get the number of open connections for a specific context."""
    return __active_connections[context]


def build_db_url(db_config: DbConfig) -> URL:
    """Assemble the SQLAlchemy connection URL from the plain `DbConfig` fields.

    `mitup_bot.config` must stay importable without SQLAlchemy installed, so URL construction
    lives with the engine here rather than as a property on `DbConfig`.
    """
    return URL.create(
        drivername=db_config.url_schema,
        username=db_config.username,
        password=db_config.password.get_secret_value(),
        host=db_config.url,
        port=db_config.port,
        database=db_config.database,
    )


def configure_db(db_config: DbConfig, skip_if_initialized: bool = False, metrics_client: MetricsClient | None = None):
    """Configure the db module by creating the engine and the session factory.

    Passing a `metrics_client` enables connection-pool observability: pool-event gauges plus
    the checkout wait time and pool-timeout counters emitted by `begin()`. It both switches the
    sampling on and takes the samples raised outside any instrumented invocation — see
    `record_pool_sample`. Callers without a metrics pipeline (CLI commands, unit tests) omit it
    and get an uninstrumented pool.
    """
    global __sessionmaker, __pool_metrics

    if __sessionmaker is not None and not skip_if_initialized:
        raise DbAlreadyInitializedError()

    engine = create_async_engine(
        build_db_url(db_config),
        echo=db_config.engine_echo,
        json_serializer=serialize_pydantic_model,
        json_deserializer=deserialize_pydantic_model,
        pool_size=db_config.pool_size,
        max_overflow=db_config.max_overflow,
        pool_timeout=db_config.pool_timeout,
    )
    __pool_metrics = metrics_client
    if metrics_client is not None:
        instrument_pool(engine, metrics_client)
    # expire_on_commit=False: nothing reads ORM objects after the transaction commits, and
    # expired attributes would otherwise trigger implicit (greenlet-unsafe) loads on access.
    __sessionmaker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def checked_out_connections(sync_engine: Engine) -> int:
    pool = sync_engine.pool
    # create_async_engine always builds an AsyncAdaptedQueuePool, a QueuePool subclass.
    assert isinstance(pool, QueuePool)
    return pool.checkedout()


def record_pool_sample(
    configured: MetricsClient | None,
    name: MetricKey,
    value: float = 1.0,
    unit: MetricUnit = MetricUnit.COUNT,
):
    """Write one connection-pool sample onto the client accountable for it.

    Inside an instrumented invocation that is the invocation's own client, so the sample joins its
    flush window and inherits the correlation key already on those records — `update_id` in the
    bot, `run_id` in the events runner — and pool pressure is readable against the update or run
    that raised it. Outside one — the web layer serving a Patreon request, a CLI command — the
    sample rides `configured`, the client `configure_db` was given, which also gates the sampling:
    without it the pool is uninstrumented.

    An invocation's own dimensions ride the sample as EMF properties rather than as dimensions:
    the pool series are process-wide and their alarms read them dimensionless, so which invocation
    a sample was attributed to must not decide which series it lands in.
    """
    if configured is None:
        return
    client = current_metrics_client() or configured
    client.emit_aggregate(name, value, unit)


def instrument_pool(engine: AsyncEngine, metrics: MetricsClient):
    """Attach pool-event listeners emitting the in-use gauge and connection-open counter.

    The listeners are synchronous (SQLAlchemy pool events) so they only accumulate records; the
    client each sample lands on is flushed by whoever owns it — the invocation on the stack, or
    `begin()` once per transaction outside one, after the checkin has fired. SQLAlchemy drives
    them in the greenlet running the async connection, which carries the awaiting task's context,
    so the invocation's client is visible from inside them.
    """
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def emit_connection_opened(dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry):
        record_pool_sample(metrics, MetricKey.DB_POOL_CONNECTIONS_OPENED)

    @event.listens_for(sync_engine, "checkout")
    def emit_checkout_gauge(
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry,
        connection_proxy: PoolProxiedConnection,
    ):
        record_pool_sample(metrics, MetricKey.DB_POOL_CONNECTIONS_IN_USE, checked_out_connections(sync_engine))

    @event.listens_for(sync_engine, "checkin")
    def emit_checkin_gauge(dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry):
        # The checkin event fires before the pool's bookkeeping releases the connection, so
        # subtract the one being returned to report the post-release level (reaching 0 idle).
        record_pool_sample(metrics, MetricKey.DB_POOL_CONNECTIONS_IN_USE, checked_out_connections(sync_engine) - 1)


async def acquire_timed_connection(session: AsyncSession):
    """Eagerly check out the transaction's connection, measuring the pool wait.

    Without this the checkout happens lazily on the first statement, which would smear pool
    waits (and pool-timeout errors) across arbitrary handler code instead of surfacing them
    deterministically at transaction start.
    """
    checkout_started = time.perf_counter()
    try:
        await session.connection()
    except PoolTimeoutError:
        record_pool_sample(__pool_metrics, MetricKey.DB_POOL_TIMEOUT)
        raise
    record_pool_sample(
        __pool_metrics,
        MetricKey.DB_POOL_CHECKOUT_WAIT_TIME,
        (time.perf_counter() - checkout_started) * 1000,
        MetricUnit.MILLISECONDS,
    )


@asynccontextmanager
async def begin() -> AsyncGenerator[AsyncSession]:
    if __sessionmaker is None:
        raise DbNotInitializedError()

    async with __sessionmaker() as session:
        context = __connection_context.get()
        # Single-threaded event loop and no await between read and write, so the
        # increment/decrement pairs cannot interleave.
        __active_connections[context] += 1
        try:
            async with session.begin():
                await acquire_timed_connection(session)
                # Anything in here is considered to be in a transaction
                # Any fault raised when this context is handled will trigger a rollback
                # in the ongoing transaction
                yield session
        finally:
            __active_connections[context] -= 1
            if __pool_metrics is not None and current_metrics_client() is None:
                # The commit above released the connection, so this transaction's checkin gauge is
                # already accumulated: one EMF line flushed per transaction. Only the samples that
                # had no invocation to ride are this layer's to flush — an invocation closes the
                # window its own samples joined.
                await __pool_metrics.flush()


def loaded_attributes(obj: object) -> set[str]:
    state = sa_inspect(obj)
    assert state is not None
    return set(state.dict)


async def racy_flush[T](session: AsyncSession, builder: Callable[[], T], *, constraint: str) -> T | None:
    """Flush rows that may lose a uniqueness race, without poisoning the outer transaction.

    ``builder`` must construct the racy rows inside the ``begin_nested()`` savepoint it runs
    under — construction makes them session-pending via relationship cascades, which is what
    lets a clash roll them back cleanly, reload the touched collections, and return ``None``.
    An ``IntegrityError`` naming any other constraint re-raises.

    >>> link = await racy_flush(
    ...     session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
    ... )
    """
    # Snapshot which attributes each persistent object has loaded before the savepoint: a
    # rollback resets whatever the builder dirtied (relationship collections appended to via
    # backrefs, mutated scalars) to the unloaded state, and the async engine cannot reload
    # them lazily on access — User's collections are lazy="raise" on top of that.
    loaded_before = [(obj, loaded_attributes(obj)) for obj in session.identity_map.values()]
    try:
        async with session.begin_nested():
            built = builder()
            if isinstance(built, SQLModel):
                # The builder wires the new row up through relationship assignments, which only
                # append it to the parent collections via backref events — SQLAlchemy 2.0 does
                # not cascade backref-only associations into the session (it warns and skips
                # the INSERT). Add the built row explicitly; its own save-update cascade then
                # carries any other transient rows it references (e.g. the invite path's new
                # User).
                session.add(built)
            await session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if diag is None or diag.constraint_name != constraint:
            raise
        # Reload exactly what the rollback unloaded (the explicit refresh is the sanctioned
        # loading route for lazy="raise" relationships): this drops the phantom rows from the
        # in-memory collections and picks up whatever the concurrent transaction committed.
        for obj, loaded in loaded_before:
            unloaded_by_rollback = loaded - loaded_attributes(obj)
            if unloaded_by_rollback:
                await session.refresh(obj, list(unloaded_by_rollback))
        return None
    return built


class _WriteHandlerDecorator(Protocol):
    def __call__[**P, R](
        self, func: Callable[Concatenate[AsyncSession, P], Coroutine[Any, Any, R]], /
    ) -> Callable[P, Coroutine[Any, Any, R]]: ...


def capture_api(args: Sequence[object], kwargs: Mapping[str, object]) -> WriteApi[Any]:
    """Take the api to capture on from the handler's context: handlers follow the
    ``(session, update, context)`` convention, so at the call site the context is the last
    positional argument (or an explicit ``context=`` keyword). The check is structural
    (does the candidate's ``.api`` expose the capture lifecycle?) because this module only
    knows the api through the `WriteApi` protocol."""
    candidate = kwargs.get("context", args[-1] if args else None)
    api = getattr(candidate, "api", None)
    if api is not None and callable(getattr(api, "begin_capture", None)):
        return cast("WriteApi[Any]", api)
    raise TypeError(
        "with_session(write=True) requires the MitupContext (exposing `.api`) as the last positional "
        "argument or the `context` keyword; non-handler code uses db.begin_write(api) directly"
    )


async def apply_reconcile(api: WriteApi[Any], outbox: OutboxProtocol):
    """Run the registered reconciler over the DB fix-ups discovered while draining the
    outbox, in one short transaction; a drain that recorded nothing skips the transaction."""
    if not (outbox.dead_message_ids or outbox.inactive_tg_user_ids or outbox.confirmed_render_digests):
        return
    assert __outbox_reconciler is not None, "begin_write refuses to start without a registered reconciler"
    async with begin() as session:
        await __outbox_reconciler(session, api.adapter, outbox)


@asynccontextmanager
async def begin_write[OutboxT: OutboxProtocol](api: WriteApi[OutboxT]) -> AsyncGenerator[AsyncSession]:
    """Run one write-mode critical section: the api is in capture mode for the body (every
    ``api.*`` call enqueues a plain-data snapshot), the transaction commits — releasing the
    pooled connection and any per-meeting row lock — and only then do the queued Telegram
    calls drain, followed by their reconcile fix-ups in one short follow-up transaction.

    This is the primitive behind ``with_session(write=True)``. Non-handler code (CLI batch
    jobs) uses it directly, wrapping each per-meeting critical section:

    >>> async with begin_write(api) as session:
    ...     meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    ...     await api.update_meeting_messages(meeting=meeting)

    A body exception discards the queue along with the rolled-back transaction — nothing
    about aborted state is rendered.
    """
    if __outbox_reconciler is None:
        raise OutboxReconcilerNotRegisteredError()
    token = WRITE_STATE.set(WriteState(WritePhase.BODY, committed=False))
    outbox = api.begin_capture()
    try:
        async with begin() as session:
            yield session
    finally:
        api.end_capture()
    # The transaction is committed and its locks are released; only now run the captured
    # fan-out. The drain reports its own failures instead of raising, and the reconcile applies
    # whatever fix-ups it recorded — including those from a drain that gave up partway.
    WRITE_STATE.set(WriteState(WritePhase.POST_COMMIT_FAN_OUT, committed=True))
    try:
        await api.execute_queued(outbox)
    finally:
        await apply_reconcile(api, outbox)
    WRITE_STATE.reset(token)


@overload
def with_session[**P, R](
    func: Callable[Concatenate[AsyncSession, P], Coroutine[Any, Any, R]], /
) -> Callable[P, Coroutine[Any, Any, R]]: ...


@overload
def with_session(*, write: Literal[True]) -> _WriteHandlerDecorator: ...


def with_session(func: Callable | None = None, /, *, write: bool = False) -> Callable:
    """Wrap an async function in a database transaction, injecting the open `AsyncSession`
    as its first positional argument; commits on clean return, rolls back on exception.

    ``write=True`` is the two-phase mode: the decorator runs the handler inside
    ``begin_write(context.api)``, which captures the ``context.api`` calls, commits, then
    drains the queue and applies its reconcile fix-ups — see the database skill for the
    full lifecycle. A handler exception discards the queue along with the rolled-back
    transaction.

    >>> @with_session
    ... async def handler(session: AsyncSession, *args, **kwargs): ...
    """

    def decorate(func: Callable) -> Callable:
        if not write:

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                async with begin() as session:
                    return await func(session, *args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        async def write_wrapper(*args, **kwargs):
            async with begin_write(capture_api(args, kwargs)) as session:
                return await func(session, *args, **kwargs)

        return write_wrapper

    return decorate if func is None else decorate(func)
