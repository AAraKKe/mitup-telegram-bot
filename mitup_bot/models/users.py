import datetime as dt
from typing import TYPE_CHECKING, Literal, Self, overload

from sqlalchemy import Column, DateTime, FetchedValue
from sqlmodel import Field, Relationship, Session, SQLModel, select
from telegram.ext import ExtBot

from mitup_bot.exceptions import UserNotFound
from mitup_bot.views import MitupView

from . import JoinedUsers, Meetup
from .base_model import BaseModel

if TYPE_CHECKING:
    from . import JoinedUsers, Meetup, Settings


class User(BaseModel, SQLModel, table=True):
    # Until better configuration is available through SQLModel (https://github.com/tiangolo/sqlmodel/issues/159)
    __tablename__: str = "users"

    first_name: str
    tg_user_id: int
    id: int | None = Field(default=None, primary_key=True)
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
    is_active: bool = True
    last_name: str | None = None
    username: str | None = None
    settings: Settings = Relationship(
        back_populates="user",
        cascade_delete=True,
        sa_relationship_kwargs={"uselist": False},
    )
    meetups: list[Meetup] = Relationship(back_populates="owner", cascade_delete=True)
    joined_links: list[JoinedUsers] = Relationship(
        back_populates="user",
        cascade_delete=True,
        sa_relationship_kwargs={"foreign_keys": "JoinedUsers.user_id"},
    )

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, User) else NotImplemented

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

    @property
    def inline_name(self) -> str:
        """
        Name to use for the user in inline messages.

        If the user has a username, use that, otherwise fall back to first name.
        """
        return self.username or self.first_name

    @property
    def lang(self) -> str:
        return self.settings.language

    def joined_meeting(self, meeting_id: int) -> JoinedUsers | None:
        joined_links = [joined for joined in self.joined_links if joined.meetup_id == meeting_id]
        return joined_links[0] if joined_links else None

    def own_meeting(self, meeting_id: int) -> Meetup | None:
        return next((meetup for meetup in self.meetups if meetup.db_id == meeting_id), None)

    def datetime_in_tz(self, datetime: dt.datetime) -> dt.datetime:
        return datetime.astimezone(self.settings.tz)

    def now_in_tz(self) -> dt.datetime:
        return self.datetime_in_tz(dt.datetime.now(dt.UTC))

    async def send_message(self, bot: ExtBot, view: MitupView):
        await bot.send_message(
            chat_id=self.tg_user_id,
            text=view.description.text,
            entities=view.description.entities or None,
            reply_markup=view.markup,
        )
