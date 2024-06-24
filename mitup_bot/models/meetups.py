import datetime as dt
import logging
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast, overload
from zoneinfo import ZoneInfo

from pydantic.config import ConfigDict
from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, Session, SQLModel, select
from telegram import Update

from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.utils import ButtonMessages, Emojis, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupInlineView, MitupView
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard

from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from .joined_users import JoinedUsers
    from .messages import Message
    from .users import User


class MeetupLocation(MutableModel):
    name: str | None = None
    coordinates: tuple[float, float] | None = None

    # Make sure to forbid extra parameters to not allow serialization of randome strigns
    # since both name and coordinates are optional
    model_config: ClassVar[ConfigDict] = {"extra": "forbid"}

    @property
    def coerced_name(self) -> str | None:
        """Provides a name coercing to None if it is an empty string"""
        if self.name is None:
            return None

        return None if len(self.name.strip()) == 0 else self.name

    def __str__(self) -> str:
        match self.coerced_name, self.coordinates:
            case (None, None):
                return MeetingMessages.LOCATION_NOT_SET.get()
            case _:
                name_section = f"{self.coerced_name}" if self.coerced_name else ""
                coordinates_section = f"[{Emojis.PIN}]" if self.coordinates else ""
                return f"{name_section} {coordinates_section}".strip()


class Meetup(SQLModel, table=True):
    __tablename__: str = "meetups"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int | None = Field(default=None, foreign_key="users.id")
    title: str | None = None
    description: str | None = None
    created_time: dt.datetime | None = None
    updated_time: dt.datetime | None = None
    datetime: dt.datetime | None = None
    max_members: int | None = None
    language: str | None = None
    location: MeetupLocation = Field(
        default=MeetupLocation(),
        sa_column=Column(type_=MeetupLocation.as_mutable(JSON(none_as_null=True)), nullable=True),
    )
    active: bool = True

    owner: "User" = Relationship(back_populates="meetups")
    messages: list["Message"] = Relationship(back_populates="meetups")
    joined_links: list["JoinedUsers"] = Relationship(back_populates="meetup")

    @property
    def full(self) -> bool:
        if self.max_members is None:
            return False
        return len([link for link in self.joined_links if not link.is_waiting_list]) >= self.max_members

    def has_message(self, update: Update) -> bool:
        logging.info("------- HAS MESSAGE -------")
        if eff_message := update.effective_message:
            logging.info(f"Checking message {eff_message.message_id!r} in {self.messages}")
            return any(message.message_id == eff_message.message_id for message in self.messages)
        if update.callback_query and update.callback_query.inline_message_id:
            return any(
                message.inline_message_id == update.callback_query.inline_message_id for message in self.messages
            )
        return False

    @property
    def timezone(self) -> ZoneInfo:
        return self.owner.settings.tz

    @property
    def datetime_in_tz(self) -> dt.datetime | None:
        return self.owner.datetime_in_tz(self.datetime) if self.datetime else None

    @property
    def str_datetime(self) -> str:
        if self.datetime:
            return f"{self.datetime_in_tz:%Y-%m-%d %H:%M} ({self.timezone.key})"
        return MeetingMessages.DATE_NOT_SET.get()

    @property
    def participants_text(self) -> str:
        if len(self.joined_links) == 0:
            total_participants = MeetingMessages.EMPTY.get(lang=self.lang)
        elif len(self.joined_links) == 1:
            total_participants = f"1 {MeetingMessages.PARTICIPANT.get(lang=self.lang)}"
        else:
            total_participants = f"{len(self.joined_links)} {MeetingMessages.PARTICIPANTS.get(lang=self.lang)}"

        max_participants = (
            MeetingMessages.MAX_PARTICIPANTS.get(lang=self.lang, max_participants=self.max_members)
            if self.max_members
            else ""
        )

        participant_list = [link.user.inline_name for link in self.joined_links]
        participants_message = f"\n{"\n\t".join(participant_list)}" if participant_list else ""

        return f"{total_participants} {max_participants}{participants_message}"

    @property
    def message(self) -> str:
        return MeetingMessages.FEATURES.get(
            title=self.title,
            lang=self.lang,
            owner=self.owner.username or self.owner.first_name,
            description=self.description or MeetingMessages.DESCRIPTION_NOT_SET.get(lang=self.lang),
            datetime=self.str_datetime,
            location=str(self.location) or MeetingMessages.LOCATION_NOT_SET.get(lang=self.lang),
            participants=self.participants_text,
        )

    @property
    def main_view(self) -> MitupView:
        return MitupView(
            self.message,
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.JOIN.get(lang=self.user_language),
                        callback_data=cb.JOIN.with_id(cast(int, self.id)),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.INVITE.get(lang=self.user_language),
                        callback_data=cb.INVITE.with_id(cast(int, self.id)),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.LEAVE.get(lang=self.user_language),
                        callback_data=cb.LEAVE.with_id(cast(int, self.id)),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.EDIT.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING.with_id(cast(int, self.id)),
                    ),
                    ButtonConfig(text=ButtonMessages.CHAT.get(lang=self.user_language), callback_data=cb.CHAT),
                    ButtonConfig(
                        text=ButtonMessages.DELETE.get(lang=self.user_language),
                        callback_data=cb.DELETE_MEETING.with_id(cast(int, self.id)),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.SHARE.get(lang=self.user_language), switch_inline_query=str(self.id)
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.get(lang=self.user_language), callback_data=cb.MAIN_MENU
                    ),
                ],
            ],
        )

    @property
    def external_view(self) -> MitupView:
        """This is the view shown to users that do not own the meeting when checking through meetings I have joined"""
        return MitupView(
            self.message,
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.JOIN.get(lang=self.user_language),
                        callback_data=cb.JOIN.with_id(cast(int, self.id)),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.INVITE.get(lang=self.user_language),
                        callback_data=cb.INVITE.with_id(cast(int, self.id)),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.LEAVE.get(lang=self.user_language),
                        callback_data=cb.LEAVE.with_id(cast(int, self.id)),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.get(lang=self.user_language), callback_data=cb.MAIN_MENU
                    ),
                ],
            ],
        )

    @property
    def edit_view(self) -> MitupView:
        assert self.id is not None, "View cannot be generated without id"

        now_in_tz: dt.datetime = self.datetime_in_tz or self.owner.now_in_tz()

        return MitupView(
            self.message,
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.TITLE.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_TITLE.with_id(self.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DESCRIPTION.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.DATE.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_DATE.with_id(self.id).with_date(now_in_tz.date()),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.CLOCK.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_TIME.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.PARTICIPANTS.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(self.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.LOCATION.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_LOCATION.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.LANGUAGE.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(self.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.SETTINGS.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_SETTINGS.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.DONE.get(lang=self.user_language),
                        callback_data=cb.SHOW_MEETING.with_id(self.id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.get(lang=self.user_language), callback_data=cb.MAIN_MENU
                    ),
                ],
            ],
        )

    @property
    def inline_view(self) -> MitupInlineView:
        return MitupInlineView(
            self.message,
            self.build_inline_keyboard(),
            id=str(self.id),
            title=str(self.title),
        )

    def build_inline_keyboard(self) -> Keyboard:
        return [
            [
                ButtonConfig(
                    text=ButtonMessages.JOIN.get(lang=self.user_language),
                    callback_data=cb.JOIN.with_id(cast(int, self.id)),
                ),
                ButtonConfig(
                    text=ButtonMessages.LEAVE.get(lang=self.user_language),
                    callback_data=cb.LEAVE.with_id(cast(int, self.id)),
                ),
            ],
        ]

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

    @property
    def user_language(self) -> str:
        return self.owner.settings.language

    @property
    def lang(self) -> str:
        """Safe way of getting the langauge of the meeting. If it is not set, it will default to the user's language."""
        return self.language or self.user_language
