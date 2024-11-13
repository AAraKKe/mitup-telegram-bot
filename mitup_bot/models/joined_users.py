import datetime as dt
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:  # pragma: no cover
    from . import Meetup, User


class JoinedUsers(SQLModel, table=True):
    __tablename__ = "joined_users"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    meetup_id: int | None = Field(default=None, foreign_key="meetups.id")
    created_time: dt.datetime = dt.datetime.now(dt.UTC)
    is_waiting_list: bool = False
    notification_sent: bool = False

    meetup: "Meetup" = Relationship(back_populates="joined_links")
    user: "User" = Relationship(back_populates="joined_links")

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, JoinedUsers) else NotImplemented
