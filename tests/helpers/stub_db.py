import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, overload
from unittest import mock

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.expression import GenerativeSelect, SelectBase
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.models import User


@dataclass
class Result:
    """This is a stub of the SQLAlchemy ScalarResult class. Implement any method we might need for testing.

    ``results`` is typed ``Any`` because ScalarResult rows are not always ORM models: scalar
    aggregates (``SELECT count(*)``), grouped ``(key, count)`` rows and ``UPDATE ... RETURNING``
    tuples all flow through here too.
    """

    results: tuple[Any, ...] = field(default_factory=tuple)
    rowcount: int | None = None

    def first(self) -> Any | None:
        return self.results[0] if self.results else None

    def all(self) -> tuple[Any, ...]:
        return self.results

    def one(self) -> Any:
        # Mirrors ScalarResult.one for scalar aggregates (e.g. `SELECT count(*)`), which the
        # sender's delivery counters read via `.one()`.
        return self.results[0]

    def __iter__(self) -> Iterator[Any]:
        # The real ScalarResult is iterable; some callers loop over the result directly
        # (e.g. grouped aggregates and the draft sweep) instead of calling .all().
        return iter(self.results)


class MockDbSession(mock.MagicMock):
    """
    A mock implementation of the database session.

    This class provides a mock implementation of the database session for testing purposes.
    It allows you to register objects, execute queries, and assert various conditions on the session.
    """

    def __init__(self, *args, **kwargs):
        # Ensure the mock is created with AsyncSession as the spec
        super().__init__(*args, spec=AsyncSession, **kwargs)
        self.add = mock.MagicMock(side_effect=self.__add_side_effect)
        # I/O methods are coroutines on AsyncSession, so they mock as AsyncMock
        self.flush = mock.AsyncMock()
        self.refresh = mock.AsyncMock()
        self.exec_return = mock.MagicMock()
        self.exec = mock.AsyncMock(side_effect=self.__exec_side_effect)
        self.delete = mock.AsyncMock()
        # racy_flush's clash recovery sweeps the identity map for expired attributes; an
        # empty real dict keeps that sweep a deterministic no-op under the mock session.
        self.identity_map: dict = {}
        self.statements_registry: dict[str, Result] = {}
        self.objects_added: list[SQLModel] = []
        self.__last_id = 0

    @staticmethod
    def normalize_query(query: str) -> str:
        """
        Normalizes a query string by removing new lines and extra spaces.

        Args:
            query: The query string to be normalized.

        Returns:
            str: The normalized query string.
        """
        return re.sub(r"\s+", " ", query)

    def __add_side_effect(self, obj: SQLModel):
        self.objects_added.append(obj)
        # Some times the id field is only generated after storing int he db.
        if hasattr(obj, "id") and obj.id is None:
            self.__last_id += 1
            obj.id = self.__last_id

    def __exec_side_effect(self, statement: SelectBase | UpdateBase) -> Result | None:
        # Participant-mutating paths load the meeting with by_id(for_update=True), while the
        # registry is keyed on the plain SELECT registered by add_object — so key the lookup on
        # a copy of the statement with its FOR UPDATE clause cleared. SQLAlchemy exposes no
        # public API to remove an applied with_for_update (the method only ever sets it), hence
        # the private _for_update_arg, declared on GenerativeSelect. The original statement is
        # not mutated, so queries_executed still exposes the locked SQL for tests that assert
        # on it.
        if isinstance(statement, GenerativeSelect) and statement._for_update_arg is not None:
            statement = statement._clone()
            statement._for_update_arg = None
        statement_str = str(statement.compile(compile_kwargs={"literal_binds": True}))
        # If we have not registered the object, return an empty result mimicking the behavior of the real session
        return self.statements_registry.get(statement_str, Result())

    def assert_not_flushed(self):
        """
        Asserts that the `flush` method has not been called.
        """
        self.flush.assert_not_called()

    def assert_flushed(self, times: int | None = None):
        """
        Asserts that the `flush` method has been called.

        With no argument, asserts a single flush — the common case, e.g. the savepoint flush
        inside ``racy_flush`` on the join path. Pass ``times`` when a path stacks several
        explicit flushes (such as registering a new user before joining them).
        """
        if times is None:
            self.flush.assert_called_once()
        else:
            assert self.flush.call_count == times, (
                f"Expected flush to be called {times} times, called {self.flush.call_count}"
            )

    def assert_refresh(self, *objs: SQLModel):
        """
        Asserts that the `refresh` method has been called for the given objects.
        """
        self.refresh.assert_has_calls([mock.call(obj) for obj in objs], any_order=True)

    def assert_added(self, *objs: SQLModel):
        """
        Asserts that the specified objects have been added to the database.

        Args:
            *objs: Variable number of SQLModel objects to be checked. If none are provided, it asserts that the `add`
            method was called at least once.
        """
        match len(objs):
            case 0:
                self.add.assert_called()
            case 1:
                self.add.assert_called_once_with(objs[0])
            case _:
                self.add.assert_has_calls([mock.call(obj) for obj in objs], any_order=True)

    def assert_not_added(self):
        """
        Asserts that the `add` method was not called.
        """
        self.add.assert_not_called()

    def assert_deleted(self, *objs: SQLModel):
        """
        Asserts that the given objects have been deleted.

        Args:
            *objs: Variable number of SQLModel objects to be checked. If none are provided, it asserts that the `delete`
            method was called at least once.
        """
        match len(objs):
            case 0:
                self.delete.assert_called()
            case 1:
                self.delete.assert_called_once_with(objs[0])
            case _:
                self.delete.assert_has_calls([mock.call(obj) for obj in objs], any_order=True)

    def assert_not_deleted(self):
        """
        Asserts that the `delete` method has not been called.
        """
        self.delete.assert_not_called()

    @property
    def queries_executed(self) -> list[str]:
        """
        Returns a list of executed queries. Each query is represented as a string without new lines.

        Returns:
            List[str]: A list of strings representing the executed queries.
        """

        return [
            self.normalize_query(
                str(call.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            )
            for call in self.exec.call_args_list
        ]

    @overload
    def add_object(self, obj: SQLModel | None, query_field: str): ...

    @overload
    def add_object(self, obj: SQLModel | None): ...

    def add_object(self, obj: SQLModel | None, query_field: str = "id"):
        """
        Adds an object to the database.

        Args:
            obj: The object to be added to the database.
            query_field: The field to use for querying the object (default is "id").

        Raises:
            ValueError: If the object is already registered in the database.
        """
        if obj is None:
            # Just a helper method to allow parameterizing tests when object do not exist
            # By registering None we allow calling add_object for any set of parameters passing None
            # if a given object should not be registered
            return

        statement_comparisson = getattr(type(obj), query_field) == getattr(obj, query_field)
        statement = select(type(obj)).where(statement_comparisson)
        statement_str = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if statement_str in self.statements_registry:
            raise ValueError(f"The object {obj!r} is already registered!")
        self.statements_registry[statement_str] = Result(results=(obj,))

    def add_objects_with_statement(self, statement: SelectBase | UpdateBase, objects: tuple[Any, ...]):
        statement_str = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if statement_str in self.statements_registry:
            raise ValueError(f"The statement {statement_str!r} is already registered!")
        self.statements_registry[statement_str] = Result(results=objects)

    def add_user(self, user: User):
        self.add_object(user, query_field="tg_user_id")

    def add_user_from_update(self, update: Update) -> User:
        assert update.effective_user is not None

        user = User(id=1, tg_user_id=update.effective_user.id, first_name=update.effective_user.first_name)
        self.add_object(user, "tg_user_id")
        return user

    def assert_object_added(self, obj: SQLModel):
        """
        Asserts that the specified object has been added to the database through a call to the `add` method.

        Args:
            obj: The object to be checked.
        """
        assert obj in self.objects_added
