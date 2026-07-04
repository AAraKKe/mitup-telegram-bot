import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Column, DateTime, FetchedValue, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from mitup_bot.utils.entities import FormattedText, render
from mitup_bot.utils.messages import MeetingDisplayMessages

from .base_model import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from . import Meetup, User

# Name of the DB-level uniqueness guard on (user_id, meetup_id). Shared with the handler layer so the
# IntegrityError raised on a duplicate join can be told apart from any other constraint violation.
JOINED_USERS_UNIQUE_CONSTRAINT = "uq_joined_users_user_id_meetup_id"


class JoinedUsers(BaseModel, SQLModel, table=True):
    __tablename__ = "joined_users"
    __table_args__ = (UniqueConstraint("user_id", "meetup_id", name=JOINED_USERS_UNIQUE_CONSTRAINT),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    meetup_id: int | None = Field(default=None, foreign_key="meetups.id", ondelete="CASCADE")
    invited_by_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    is_waiting_list: bool = False
    notification_sent: bool = False

    # lazy="selectin" on all three: `participant_name` traverses user, invited_by and meetup in
    # plain Python, and implicit lazy loads raise MissingGreenlet under the async engine.
    meetup: Meetup = Relationship(back_populates="joined_links", sa_relationship_kwargs={"lazy": "selectin"})
    user: User = Relationship(
        back_populates="joined_links",
        sa_relationship_kwargs={"foreign_keys": "JoinedUsers.user_id", "lazy": "selectin"},
    )
    # Need to use the older Optional syntax
    invited_by: Optional[User] = Relationship(  # noqa: UP045
        sa_relationship_kwargs={"foreign_keys": "JoinedUsers.invited_by_id", "lazy": "selectin"}
    )

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, JoinedUsers) else NotImplemented

    @property
    def participant_name(self) -> FormattedText:
        name = self.user.inline_name
        if self.invited_by is not None:
            language = self.meetup.lang
            invited_by_text = MeetingDisplayMessages.INVITED_BY.get(lang=language, user=self.invited_by.inline_name)
            return render(t"{name} ({invited_by_text})")
        return FormattedText(name)
