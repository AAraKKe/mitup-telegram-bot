import datetime as dt
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast, overload

from pydantic.config import ConfigDict
from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, Session, SQLModel, select

from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.utils import ButtonMessages, Emojis, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views.mitup_view import ButtonConfig

from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from .users import User


class MeetupLocation(MutableModel):
    name: str | None = None
    coordinates: tuple[float, float] | None = None

    # Make sure to forbid extra parameters to not allow serialization of randome strigns
    # since both name and coordinates are optional
    model_config: ClassVar[ConfigDict] = {"extra": "forbid"}

    def __str__(self) -> str:
        clean_name = None if self.name is None else None if len(self.name.strip()) == 0 else self.name

        match clean_name, self.coordinates:
            case (None, None):
                return MeetingMessages.LOCATION_NOT_SET.get()
            case _:
                result = f"{clean_name} " if clean_name else ""
                result += f"[{Emojis.PIN}]" if self.coordinates else ""
                return result


class Meetup(SQLModel, table=True):
    __tablename__: str = "meetups"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int | None = Field(default=None, foreign_key="users.id")
    title: str | None = None
    description: str | None = None
    created_time: dt.datetime | None = None
    updated_time: dt.datetime | None = None
    date: dt.date | None = None
    time: dt.time | None = None
    max_members: int | None = None
    language: str = "en"
    location: MeetupLocation = Field(
        default=MeetupLocation(),
        sa_column=Column(type_=MeetupLocation.as_mutable(JSON(none_as_null=True)), nullable=True),
    )
    active: bool = True

    owner: "User" = Relationship(back_populates="meetups")

    @property
    def full(self) -> bool:
        # For now, we have not created the members field that represents the
        # users that joined a given Meeting. This is False for now until we can
        # properly determine whether it is full or not.
        # return self.max_members == len(self.members)
        return False

    @property
    def main_view(self) -> MitupView:
        features_message = MeetingMessages.FEATURES.get(
            title=self.title,
            owner=self.owner.username,
            description=self.description or MeetingMessages.DESCRIPTION_NOT_SET.get(),
            date=self.date or MeetingMessages.DATE_NOT_SET.get(),
            location=self.location or MeetingMessages.LOCATION_NOT_SET.get(),
            participants=MeetingMessages.PARTICIPANTS_NOT_SET.get(),
        )

        assert self.id is not None, "View cannot be generated without id"

        return MitupView(
            features_message,
            [
                [
                    ButtonConfig(text=ButtonMessages.JOIN.get(), callback_data=cb.JOIN),
                    ButtonConfig(text=ButtonMessages.INVITE.get(), callback_data=cb.INVITE),
                    ButtonConfig(text=ButtonMessages.LEAVE.get(), callback_data=cb.LEAVE),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(cast(int, self.id))
                    ),
                    ButtonConfig(text=ButtonMessages.CHAT.get(), callback_data=cb.CHAT),
                    ButtonConfig(text=ButtonMessages.DELETE.get(), callback_data=cb.DELETE_MEETING),
                ],
                [
                    ButtonConfig(text=ButtonMessages.SHARE.get(), callback_data=cb.SHARE),
                ],
                [
                    ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU),
                ],
            ],
        )

    @property
    def edit_view(self) -> MitupView:
        features_message = MeetingMessages.FEATURES.get(
            title=self.title,
            owner=self.owner.username,
            description=self.description or MeetingMessages.DESCRIPTION_NOT_SET.get(),
            date=self.date or MeetingMessages.DATE_NOT_SET.get(),
            location=self.location or MeetingMessages.LOCATION_NOT_SET.get(),
            participants=MeetingMessages.PARTICIPANTS_NOT_SET.get(),
        )

        assert self.id is not None, "View cannot be generated without id"

        return MitupView(
            features_message,
            [
                [
                    ButtonConfig(text=ButtonMessages.TITLE.get(), callback_data=cb.EDIT_MEETING_TITLE.with_id(self.id)),
                    ButtonConfig(
                        text=ButtonMessages.DESCRIPTION.get(),
                        callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(text=ButtonMessages.DATE.get(), callback_data=cb.EDIT_MEETING_DATE.with_id(self.id)),
                    ButtonConfig(text=ButtonMessages.CLOCK.get(), callback_data=cb.EDIT_MEETING_TIME.with_id(self.id)),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.PARTICIPANTS.get(),
                        callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(self.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.LOCATION.get(), callback_data=cb.EDIT_MEETING_LOCATION.with_id(self.id)
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.LANGUAGE.get(), callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(self.id)
                    ),
                    ButtonConfig(
                        text=ButtonMessages.SETTINGS.get(), callback_data=cb.EDIT_MEETING_SETTINGS.with_id(self.id)
                    ),
                ],
                [
                    ButtonConfig(text=ButtonMessages.DONE.get(), callback_data=cb.SHOW_MEETING.with_id(self.id)),
                ],
                [
                    ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU),
                ],
            ],
        )

    @overload
    @classmethod
    def by_id(cls, session: Session, meetup_id: int, must_exist: Literal[True]) -> Self: ...  # pragma: no cover

    @overload
    @classmethod
    def by_id(cls, session: Session, meetup_id: int, must_exist: bool = ...) -> Self | None: ...  # pragma: no cover

    @classmethod
    def by_id(cls, session: Session, meetup_id: int, must_exist: bool = False) -> Self | None:
        statement = select(cls).where(cls.id == meetup_id)
        if (found_meetup := session.exec(statement).first()) is not None:
            return found_meetup

        if must_exist:
            raise MeetupNotFound(meetup_id)

        return None
