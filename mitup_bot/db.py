from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager, suppress
from typing import Any, Concatenate, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine

from mitup_bot.config import DbConfig
from mitup_bot.models import MeetupLocation, MessageButtons

__sessionmaker: sessionmaker[Session] | None = None


class DbNotInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has not been initialized. See mitup_bot.db.configure_db for information.")


class DbAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has already been configured.")


class SessionDecorableCallback(Protocol):
    def __call__(self, *args: Any | None, db_session: Session, **kwargs: Any | None) -> Any: ...


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


def configure_db(db_config: DbConfig, skip_if_initialized: bool = False) -> None:
    """Configure the db module by creating the engine and the session factory"""
    global __sessionmaker

    if __sessionmaker is not None and not skip_if_initialized:
        raise DbAlreadyInitializedError()

    engine = create_engine(
        db_config.full_url,
        echo=db_config.engine_echo,
        json_serializer=serialize_pydantic_model,
        json_deserializer=deserialize_pydantic_model,
    )
    __sessionmaker = sessionmaker(bind=engine, class_=Session)


@contextmanager
def begin() -> Generator[Session]:
    if __sessionmaker is None:
        raise DbNotInitializedError()

    session = __sessionmaker()
    with session.begin():
        # Anything in here is considered to be in a transaction
        # Any fault raised when this context is handled will trigger a rollback
        # in the ongoing transaction
        yield session


def with_session[**P, R](func: Callable[Concatenate[Session, P], R]) -> Callable[P, R]:
    """
    Decorator that wraps a method in a database transaction. The decorated function must define
    as its first argument a `session: Session` parameter where the open session will be injected.

    Args:
        func: The method to be wrapped.

    Returns:
        The wrapped method.

    Examples:
        >>> @with_transaction
        ... def wrapper(session: Session, *args, **kwargs):
        ...     # Perform actions within a database transaction
        ...     pass
    """

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with begin() as session:
            return func(session, *args, **kwargs)

    return wrapper


def with_async_session[**P, R](
    func: Callable[Concatenate[Session, P], Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """
    Decorator that wraps an async method in a database transaction. The decorated function must define
    as its first argument a `session: Session` parameter where the open session will be injected.

    Args:
        func: The async method to be wrapped.

    Returns:
        The wrapped async method.

    Examples:
        >>> @with_async_session
        ... async def wrapper(session: Session, *args, **kwargs):
        ...     # Perform actions within a database transaction
        ...     pass
    """

    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with begin() as session:
            return await func(session, *args, **kwargs)

    return async_wrapper
