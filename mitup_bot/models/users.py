import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlalchemy import TIMESTAMP, BigInteger, Column, func
from sqlmodel import Field, Relationship, SQLModel, select

from .exceptions import MissingSessionError
from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from . import Settings


class User(MitupBaseModel, SQLModel, table=True):
    # Until better configuration is available through SQLModel (https://github.com/tiangolo/sqlmodel/issues/159)
    __tablename__: str = "users"  # type: ignore

    first_name: str
    tg_user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    id: int | None = Field(default=None, primary_key=True)
    created_time: dt.datetime | None = Field(sa_column=Column(TIMESTAMP, server_default=func.now()))
    updated_time: dt.datetime | None = Field(
        sa_column=Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    )
    last_name: str | None
    username: str | None
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

    @classmethod
    def get_settings_from_user(cls, tg_user_id: int) -> Optional["Settings"]:
        if cls.get_session() is None:
            raise MissingSessionError()

        user = cls.find_by_tg_user_id(tg_user_id)

        return user.settings if user is not None else None
