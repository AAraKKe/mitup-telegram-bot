import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlmodel import Field, Relationship, SQLModel

from mitup_bot.utils.messages import MeetingMessages

from .base_model import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from . import Meetup, User


class JoinedUsers(BaseModel, SQLModel, table=True):
    __tablename__ = "joined_users"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    meetup_id: int | None = Field(default=None, foreign_key="meetups.id", ondelete="CASCADE")
    invited_by_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime = dt.datetime.now(dt.UTC)
    is_waiting_list: bool = False
    notification_sent: bool = False

    meetup: Meetup = Relationship(back_populates="joined_links")
    user: User = Relationship(
        back_populates="joined_links", sa_relationship_kwargs={"foreign_keys": "JoinedUsers.user_id"}
    )
    # Need to use the older Optional syntax
    invited_by: Optional[User] = Relationship(sa_relationship_kwargs={"foreign_keys": "JoinedUsers.invited_by_id"})  # noqa: UP045

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, JoinedUsers) else NotImplemented

    @property
    def participant_name(self) -> str:
        name = self.user.inline_name
        if self.invited_by is not None:
            language = self.meetup.lang
            invited_by_str = MeetingMessages.INVITED_BY_USER.get(lang=language, user=self.invited_by.inline_name)
            name += f" ({invited_by_str})"
        return name
