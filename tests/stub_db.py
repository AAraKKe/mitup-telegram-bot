from dataclasses import dataclass
from typing import Protocol, TypeVar, overload
from unittest import mock

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.expression import SelectBase
from sqlmodel import Session, SQLModel, select
from telegram import Update

from mitup_bot.models import User


class ModelType(Protocol):
    id: int


TM = TypeVar("TM", bound=ModelType)


@dataclass
class Result:
    """This is a stub of the SQLAlchemy ScalarResult class. Implement any method we might need for testing"""

    results: tuple[SQLModel, ...]

    def first(self) -> SQLModel | None:
        return self.results[0] if self.results else None


class MockDbSession(mock.MagicMock):
    """
    A mock implementation of the database session.

    This class provides a mock implementation of the database session for testing purposes.
    It allows you to register objects, execute queries, and assert various conditions on the session.
    """

    def __init__(self, *args, **kwargs):
        # Ensure the mock is created with Sesison as the spec
        super().__init__(*args, spec=Session, **kwargs)
        self.add = mock.MagicMock()
        self.flush = mock.MagicMock()
        self.exec = mock.MagicMock(side_effect=self.__exec_side_effect)
        self.delete = mock.MagicMock()
        self.statements_registry: dict[str, Result] = {}

    def __exec_side_effect(self, statement: SelectBase) -> Result | None:
        statement_str = str(statement.compile(compile_kwargs={"literal_binds": True}))
        # If we have not registered the object, return an empty result mimicking the behavior of the real session
        return self.statements_registry.get(statement_str, Result(results=()))

    def assert_not_flushed(self):
        """
        Asserts that the `flush` method has not been called.
        """
        self.flush.assert_not_called()

    def assert_flushed(self):
        """
        Asserts that the `flush` method has been called exactly once.
        """
        self.flush.assert_called_once()

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
            *objs: Variable number of SQLModel objects to be checked for deletion.
        """
        if len(objs) > 1:
            self.delete.assert_has_calls([mock.call(obj) for obj in objs], any_order=True)
        else:
            self.delete.assert_called_once_with(objs[0])

    def assert_not_deleted(self):
        """
        Asserts that the `delete` method has not been called.
        """
        self.delete.assert_not_called()

    @property
    def queries_executed(self):
        """
        Returns a list of executed queries.

        Returns:
            List[str]: A list of strings representing the executed queries.
        """

        return [
            str(call.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
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

    def add_objects_with_statement(self, statement: SelectBase, objects: tuple[SQLModel, ...]):
        statement_str = str(statement.compile(compile_kwargs={"literal_binds": True}))
        if statement_str in self.statements_registry:
            raise ValueError(f"The statement {statement_str!r} is already registered!")
        self.statements_registry[statement_str] = Result(results=objects)

    def add_user_from_update(self, update: Update) -> User:
        assert update.effective_user is not None

        user = User(id=1, tg_user_id=update.effective_user.id, first_name=update.effective_user.first_name)
        self.add_object(user, "tg_user_id")
        return user
