from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from typing import Any, Concatenate, Protocol

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, create_engine

from mitup_bot.config import DbConfig

__sessionmaker: sessionmaker[Session] | None = None


class DbNotInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has not been initialized. See mitup_bot.db.configure_db for information.")


class DbAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("Database has already been configured.")


class SessionDecorableCallback(Protocol):
    def __call__(self, *args: Any | None, db_session: Session, **kwargs: Any | None) -> Any: ...


def configure_db(db_config: DbConfig):
    """Configure the db module by creating the engine and the session factory"""
    global __sessionmaker

    if __sessionmaker is not None:
        raise DbAlreadyInitializedError()

    engine = create_engine(db_config.full_url, echo=db_config.engine_echo)
    __sessionmaker = sessionmaker(bind=engine, class_=Session)


@contextmanager
def begin() -> Generator[Session, None, None]:
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
