import functools
import time
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any, Concatenate, Literal, Protocol, overload

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry, PoolProxiedConnection, QueuePool
from sqlmodel import SQLModel, col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.api_wrapper import ApiOutbox, TelegramApi, TelegramApiWrapper
from mitup_bot.config import DbConfig
from mitup_bot.models import MeetupLocation, Message, MessageButtons, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

log = structlog.get_logger(__name__)

__sessionmaker: async_sessionmaker[AsyncSession] | None = None
__connection_context: ContextVar[str] = ContextVar("connection_context", default="unknown")
__active_connections: Counter[str] = Counter()
__pool_metrics: MetricsClient | None = None


class DbNotInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has not been initialized. See mitup_bot.db.configure_db for information.")


class DbAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has already been configured.")


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


def set_connection_context(context: str):
    __connection_context.set(context)


def get_open_connections(context: str) -> int:
    """Get the number of open connections for a specific context."""
    return __active_connections[context]


def configure_db(db_config: DbConfig, skip_if_initialized: bool = False, metrics_client: MetricsClient | None = None):
    """Configure the db module by creating the engine and the session factory.

    Passing a `metrics_client` enables connection-pool observability: pool-event gauges plus
    the checkout wait time and pool-timeout counters emitted by `begin()`. Callers without a
    metrics pipeline (CLI commands, unit tests) omit it and get an uninstrumented pool.
    """
    global __sessionmaker, __pool_metrics

    if __sessionmaker is not None and not skip_if_initialized:
        raise DbAlreadyInitializedError()

    engine = create_async_engine(
        db_config.full_url,
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


def instrument_pool(engine: AsyncEngine, metrics: MetricsClient):
    """Attach pool-event listeners emitting the in-use gauge and connection-open counter.

    The listeners are synchronous (SQLAlchemy pool events) so they only accumulate records;
    `begin()` flushes the shared client once per transaction, after the checkin has fired.
    """
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def emit_connection_opened(dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry):
        metrics.emit(MetricKey.DB_POOL_CONNECTIONS_OPENED)

    @event.listens_for(sync_engine, "checkout")
    def emit_checkout_gauge(
        dbapi_connection: DBAPIConnection,
        connection_record: ConnectionPoolEntry,
        connection_proxy: PoolProxiedConnection,
    ):
        metrics.emit(MetricKey.DB_POOL_CONNECTIONS_IN_USE, checked_out_connections(sync_engine))

    @event.listens_for(sync_engine, "checkin")
    def emit_checkin_gauge(dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry):
        # The checkin event fires before the pool's bookkeeping releases the connection, so
        # subtract the one being returned to report the post-release level (reaching 0 idle).
        metrics.emit(MetricKey.DB_POOL_CONNECTIONS_IN_USE, checked_out_connections(sync_engine) - 1)


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
        if __pool_metrics is not None:
            __pool_metrics.emit(MetricKey.DB_POOL_TIMEOUT)
        raise
    if __pool_metrics is not None:
        __pool_metrics.emit(
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
            if __pool_metrics is not None:
                # The commit above released the connection, so this transaction's checkin
                # gauge is already accumulated: one EMF line flushed per transaction.
                await __pool_metrics.flush()


def _loaded_attributes(obj: object) -> set[str]:
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
    loaded_before = [(obj, _loaded_attributes(obj)) for obj in session.identity_map.values()]
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
            unloaded_by_rollback = loaded - _loaded_attributes(obj)
            if unloaded_by_rollback:
                await session.refresh(obj, list(unloaded_by_rollback))
        return None
    return built


class _WriteHandlerDecorator(Protocol):
    def __call__[**P, R](
        self, func: Callable[Concatenate[AsyncSession, P], Coroutine[Any, Any, R]], /
    ) -> Callable[P, Coroutine[Any, Any, R]]: ...


def _capture_api(args: Sequence[object], kwargs: Mapping[str, object]) -> TelegramApi:
    """Take the api to capture on from the handler's context: handlers follow the
    ``(session, update, context)`` convention, so at the call site the context is the last
    positional argument (or an explicit ``context=`` keyword)."""
    candidate = kwargs.get("context", args[-1] if args else None)
    api = getattr(candidate, "api", None)
    if isinstance(api, TelegramApi):
        return api
    raise TypeError(
        "with_session(write=True) requires the MitupContext (exposing `.api`) as the last positional "
        "argument or the `context` keyword; non-handler code uses db.begin_write(api) directly"
    )


async def _apply_reconcile(api: TelegramApiWrapper, outbox: ApiOutbox):
    """Apply the DB fix-ups discovered while draining the outbox, in one short transaction:
    drop Message rows Telegram reported gone and mark unreachable users inactive."""
    if not outbox.dead_message_ids and not outbox.inactive_tg_user_ids:
        return
    async with begin() as session:
        if outbox.dead_message_ids:
            log.info("Deleting messages reported gone during fan-out", message_ids=outbox.dead_message_ids)
            await session.exec(  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                delete(Message).where(col(Message.id).in_(outbox.dead_message_ids))
            )
        for tg_user_id in dict.fromkeys(outbox.inactive_tg_user_ids):
            user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
            if user is not None and user.mark_inactive():
                log.info("Marking user as inactive", tg_user_id=tg_user_id)
                api.adapter.emit_metric(MetricKey.INACTIVE_USER_SET)


@asynccontextmanager
async def begin_write(api: TelegramApiWrapper) -> AsyncGenerator[AsyncSession]:
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
    outbox = api.begin_capture()
    try:
        async with begin() as session:
            yield session
    finally:
        api.end_capture()
    # The transaction is committed and its locks are released; only now run the captured
    # fan-out. The reconcile applies whatever fix-ups were recorded even when a systemic
    # failure aborts the drain midway.
    try:
        await api.execute_queued(outbox)
    finally:
        await _apply_reconcile(api, outbox)


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
            async with begin_write(_capture_api(args, kwargs)) as session:
                return await func(session, *args, **kwargs)

        return write_wrapper

    return decorate if func is None else decorate(func)
