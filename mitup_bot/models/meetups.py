import datetime as dt
from typing import TYPE_CHECKING, Self

from sqlmodel import Field, Relationship, Session, SQLModel, desc, select

from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views.mitup_view import ButtonConfig

if TYPE_CHECKING:
    from .users import User


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
    location: str | None = None
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

        return MitupView(
            features_message,
            [
                [
                    ButtonConfig(text=ButtonMessages.JOIN.get(), callback_data=cb.JOIN),
                    ButtonConfig(text=ButtonMessages.INVITE.get(), callback_data=cb.INVITE),
                    ButtonConfig(text=ButtonMessages.LEAVE.get(), callback_data=cb.LEAVE),
                ],
                [
                    ButtonConfig(text=ButtonMessages.EDIT.get(), callback_data=cb.EDIT_MEETING),
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
                    ButtonConfig(text=ButtonMessages.TITLE.get(), callback_data=cb.EDIT_MEETING_TITLE),
                    ButtonConfig(text=ButtonMessages.DESCRIPTION.get(), callback_data=cb.EDIT_MEETING_DESCRIPTION),
                ],
                [
                    ButtonConfig(text=ButtonMessages.DATE.get(), callback_data=cb.EDIT_MEETING_DATE),
                    ButtonConfig(text=ButtonMessages.CLOCK.get(), callback_data=cb.EDIT_MEETING_TIME),
                ],
                [
                    ButtonConfig(text=ButtonMessages.PARTICIPANTS.get(), callback_data=cb.EDIT_MEETING_PARTICIPANTS),
                    ButtonConfig(text=ButtonMessages.LOCATION.get(), callback_data=cb.EDIT_MEETING_LOCATION),
                ],
                [
                    ButtonConfig(text=ButtonMessages.LANGUAGE.get(), callback_data=cb.EDIT_MEETING_LANGUAGE),
                    ButtonConfig(text=ButtonMessages.SETTINGS.get(), callback_data=cb.EDIT_MEETING_SETTINGS),
                ],
                [
                    ButtonConfig(text=ButtonMessages.DONE.get(), callback_data=cb.DONE_MEETING.with_id(self.id)),
                ],
                [
                    ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU),
                ],
            ],
        )

    @classmethod
    def by_id(cls, session: Session, meetup_id: int) -> Self | None:
        statement = select(cls).where(cls.id == meetup_id)
        if (found_meetup := session.exec(statement).first()) is not None:
            return found_meetup

        return None

    @classmethod
    def get_last_from_user(cls, session: Session, owner_id: int) -> Self | None:
        statement = select(cls).where(cls.owner_id == owner_id).order_by(desc(cls.id)).limit(1)
        if (last_meetup := session.exec(statement).first()) is not None:
            return last_meetup

        return None
