import datetime as dt
from typing import TYPE_CHECKING, Literal, Self, overload

from sqlmodel import Field, Relationship, Session, SQLModel, select

from mitup_bot.exceptions import UserNotFound

from . import Meetup

if TYPE_CHECKING:
    from . import Meetup, Settings


class User(SQLModel, table=True):
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
    meetups: list["Meetup"] = Relationship(back_populates="owner")

    @overload
    @classmethod
    def by_tg_user_id(cls, session: Session, tg_user_id: int, must_exist: Literal[True]) -> Self: ...

    @overload
    @classmethod
    def by_tg_user_id(cls, session: Session, tg_user_id: int, must_exist: bool = ...) -> Self | None: ...

    @classmethod
    def by_tg_user_id(cls, session: Session, tg_user_id: int, must_exist: bool = False) -> Self | None:
        statement = select(cls).where(cls.tg_user_id == tg_user_id)
        if (found_user := session.exec(statement).first()) is not None:
            return found_user

        if must_exist:
            raise UserNotFound(tg_user_id)

        return None

    def own_meeting(self, meeting_id: int) -> Meetup | None:  # type: ignore
        return next((meetup for meetup in self.meetups if meetup.id == meeting_id), None)
