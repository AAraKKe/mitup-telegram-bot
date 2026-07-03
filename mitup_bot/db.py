import functools
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from typing import Any, Concatenate

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.config import DbConfig
from mitup_bot.models import MeetupLocation, MessageButtons

__sessionmaker: async_sessionmaker[AsyncSession] | None = None
__connection_context: ContextVar[str] = ContextVar("connection_context", default="unknown")
__active_connections: Counter[str] = Counter()


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


def set_connection_context(context: str) -> None:
    __connection_context.set(context)


def get_open_connections(context: str) -> int:
    """Get the number of open connections for a specific context."""
    return __active_connections[context]


def configure_db(db_config: DbConfig, skip_if_initialized: bool = False) -> None:
    """Configure the db module by creating the engine and the session factory"""
    global __sessionmaker

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
    # expire_on_commit=False: nothing reads ORM objects after the transaction commits, and
    # expired attributes would otherwise trigger implicit (greenlet-unsafe) loads on access.
    __sessionmaker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


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
                # Anything in here is considered to be in a transaction
                # Any fault raised when this context is handled will trigger a rollback
                # in the ongoing transaction
                yield session
        finally:
            __active_connections[context] -= 1


def with_session[**P, R](
    func: Callable[Concatenate[AsyncSession, P], Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Wrap an async function in a database transaction, injecting the open `AsyncSession`
    as its first positional argument. Commits on clean return, rolls back on exception.

    >>> @with_session
    ... async def handler(session: AsyncSession, *args, **kwargs): ...
    """

    @functools.wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        async with begin() as session:
            return await func(session, *args, **kwargs)

    return async_wrapper
