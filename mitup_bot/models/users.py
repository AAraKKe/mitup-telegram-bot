import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlmodel import Field, Relationship, SQLModel, select

from .exceptions import MissingSessionError
from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from . import Settings


class User(MitupBaseModel, SQLModel, table=True):
    # Until better configuration is available through SQLModel (https://github.com/tiangolo/sqlmodel/issues/159)
    __tablename__: str = "users"  # type: ignore

    first_name: str
    tg_user_id: int
    id: int | None = Field(default=None, primary_key=True)
    created_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    updated_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    last_name: str | None = None
    username: str | None = None
    settings: "Settings" = Relationship(back_populates="user", sa_relationship_kwargs={"uselist": False})

    @classmethod
    def find_by_tg_user_id(cls, tg_user_id: int) -> Optional["User"]:
        session = cls.get_session()

        if session is None:
            raise MissingSessionError()

        statement = select(cls).where(cls.tg_user_id == tg_user_id)
        if (found_user := session.exec(statement).first()) is not None:
            return found_user

        return None
