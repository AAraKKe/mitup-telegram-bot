import datetime as dt
from string.templatelib import Template
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast, overload
from zoneinfo import ZoneInfo

from pydantic.config import ConfigDict
from sqlalchemy import JSON, Column, DateTime, FetchedValue
from sqlmodel import Field, Relationship, Session, SQLModel, select
from telegram import Update

from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.models import Message
from mitup_bot.utils import ButtonMessages, Emojis, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import Bold, EntityDateTime, FormattedText, render
from mitup_bot.views import MitupInlineView, MitupView
from mitup_bot.views.factory import options_button
from mitup_bot.views.mitup_view import ButtonConfig, Keyboard

from .base_model import BaseModel
from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from .joined_users import JoinedUsers
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

    def description(self, lang: str) -> FormattedText:
        match self.coerced_name, self.coordinates:
            case (None, None):
                return MeetingMessages.LOCATION_NOT_SET.get(lang=lang)
            case _:
                name_section = f"{self.coerced_name}" if self.coerced_name else ""
                coordinates_section = f"[{Emojis.PIN}]" if self.coordinates else ""
                return FormattedText(f"{name_section} {coordinates_section}".strip())

    def empty(self) -> bool:
        return self.coerced_name is None and self.coordinates is None


class Meetup(BaseModel, SQLModel, table=True):
    __tablename__: str = "meetups"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    title: str = Field(nullable=False)
    waiting_list: bool = Field(nullable=False)
    public: bool = Field(nullable=False)
    allow_invitation: bool = Field(nullable=False)
    incognito: bool = Field(nullable=False)
    expiration_notification_sent: bool = Field(nullable=False, default=False)
    end_datetime: dt.datetime | None = None
    started_notification_sent: bool = Field(nullable=False, default=False)
    lock_on_start: bool = Field(nullable=False, default=False)
    description: str | None = None
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
    expiration_time: dt.datetime | None = None
    datetime: dt.datetime | None = None
    max_members: int | None = None
    language: str | None = None
    location: MeetupLocation = Field(
        default=MeetupLocation(),
        sa_column=Column(type_=MeetupLocation.as_mutable(JSON(none_as_null=True)), nullable=True),
    )
    active: bool = True

    owner: User = Relationship(back_populates="meetups")
    messages: list[Message] = Relationship(back_populates="meetup")
    joined_links: list[JoinedUsers] = Relationship(back_populates="meetup", cascade_delete=True)

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, Meetup) else NotImplemented

    def is_owned_by(self, user: User) -> bool:
        return self.owner.db_id == user.db_id

    @property
    def n_participants(self) -> int:
        """Number of participants in the meeting. Not counting the waiting list."""
        return sum(not link.is_waiting_list for link in self.joined_links)

    @property
    def n_waiting(self) -> int:
        """Number of participants in the waiting list."""
        return sum(link.is_waiting_list for link in self.joined_links)

    @property
    def full(self) -> bool:
        return False if self.max_members is None else self.n_participants >= self.max_members

    @property
    def is_in_progress(self) -> bool:
        """Return True only when an end time is set and the current UTC time falls within the meeting window."""
        if self.end_datetime is None or self.datetime is None:
            return False
        now = dt.datetime.now(dt.UTC)
        end = self.end_datetime if self.end_datetime.tzinfo else self.end_datetime.replace(tzinfo=dt.UTC)
        start = self.datetime if self.datetime.tzinfo else self.datetime.replace(tzinfo=dt.UTC)
        return start <= now < end

    @property
    def participants(self) -> list[JoinedUsers]:
        """Get the users that have joined the meeting (not including the waiting list)"""
        return [link for link in self.joined_links if not link.is_waiting_list]

    def participant(self, user_id: int) -> JoinedUsers | None:
        return next((link for link in self.participants if link.user.db_id == user_id), None)

    def has_participant(self, user_id: int) -> bool:
        return any(link.user.db_id == user_id for link in self.joined_links)

    def remove_participant(self, participant: JoinedUsers) -> list[JoinedUsers]:
        """
        Remove a participant from the meeting. If there are users in the waiting list, they will be promoted to the
        joined list.

        If the action of removing the participant makes anyone promote, the list of promoted participants is returned.
        """
        self.joined_links.remove(participant)
        # Check if someone in the waiting list can be promoted to the joined list
        return [] if self.full else self.promote_from_waiting_list()

    def add_participant(self, user: User, invited_by: User | None = None) -> JoinedUsers | None:
        """
        Add the user to the meeting. If the meeting is full, the user will be added to the waiting list
        if it is enabled.

        If the meeting is full and the waiting list is not enabled, None will be returned.
        """

        if self.full:
            return self.create_joined_link(user, True, invited_by) if self.waiting_list else None
        return self.create_joined_link(user, False, invited_by)

    def create_joined_link(self, user: User, is_waiting_list: bool, invited_by: User | None = None) -> JoinedUsers:
        """
        Create a new JoinedUsers instance and add it to the meeting if it is not already in the list.
        """
        from mitup_bot.models import JoinedUsers

        joined_link = JoinedUsers(user=user, meetup=self, is_waiting_list=is_waiting_list, invited_by=invited_by)
        # This check is done explicitly to avoid duplicates which chan happen during tests
        # or depending on how the session is being used
        if joined_link not in self.joined_links:
            self.joined_links.append(joined_link)  # pragma: no cover
        return joined_link

    def promote_from_waiting_list(self) -> list[JoinedUsers]:
        """
        Handle promotions from the waiting list to the joined list for the given meeting.

        If the meeting is not full and waiting list is enabled, users will be promoted from the waiting list
        to the joined based on the order they joined the waiting list.
        """
        if waiting_links := self.waiting_links():
            to_promote = (
                self.n_waiting
                if self.max_members is None
                else min(self.n_waiting, self.max_members - self.n_participants)
            )
            promoted = []

            for link in waiting_links[:to_promote]:
                link.is_waiting_list = False
                promoted.append(link)

            return promoted
        return []

    def join_allowed(self) -> bool:
        return not self.full or self.waiting_list

    def waiting_links(self) -> list[JoinedUsers]:
        """Get the joined links that are in the waiting list sorted by the time they joined"""
        return sorted((link for link in self.joined_links if link.is_waiting_list), key=lambda x: x.created_time)

    def has_message(self, update: Update) -> bool:
        """Return True if the message where the update was sent from is linked to this meeting."""
        return self.message_from_update(update) is not None

    def message_from_update(self, update: Update) -> Message | None:
        """
        Get the message linked to this meeting that represents the message where the update was sent from.
        None if the message does not exist.
        """
        if eff_message := update.effective_message:
            return next((message for message in self.messages if message.message_id == eff_message.message_id), None)
        if update.callback_query and update.callback_query.inline_message_id:
            return next(
                (
                    message
                    for message in self.messages
                    if message.inline_message_id == update.callback_query.inline_message_id
                ),
                None,
            )
        return None

    def add_message(self, update: Update, user: User) -> Message:
        """Link a message to this meeting if not already linked and return the message object."""
        if (message := self.message_from_update(update)) is None:
            message = Message.from_update(update, self, user)
        return message

    @property
    def short_description(self) -> str | None:
        """A version of the meeting description used when showing the meeting on an inline query"""
        if self.description is None:
            return
        if len(self.description) <= 30:
            return self.description

        is_word_cuttoff = self.description[30] != " " and self.description[29] != " "
        if is_word_cuttoff:
            cut_description = self.description[:30].split(" ")[:-1]
            return f"{' '.join(cut_description)} ..."

        cut_description = " ".join(self.description[:30].rstrip().split(" "))
        return f"{cut_description} ..."

    @property
    def timezone(self) -> ZoneInfo:
        return self.owner.settings.tz

    @property
    def _plain_datetime(self) -> str:
        """UTC-formatted plain datetime string used in inline query previews."""
        if self.datetime:
            return f"{self.datetime:%Y-%m-%d %H:%M}"
        return MeetingMessages.DATE_NOT_SET.get_text(lang=self.lang)

    @property
    def participants_badge(self) -> Template:
        """Plain-text badge shown in inline query result descriptions."""
        empty = MeetingMessages.EMPTY.get(lang=self.lang)
        joined_count = len(self.joined_links)
        no_limit = t"({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=self.user_language)})"

        incognito_prefix = f"{Emojis.GLASSES} " if self.incognito else ""

        if self.max_members is None:
            result_badged = empty if joined_count == 0 else t"{len(self.joined_links)} {no_limit}"
            return t"{incognito_prefix}{result_badged}"

        empty_with_max = (
            t"{empty} {MeetingMessages.MAX_PARTICIPANTS.get(lang=self.lang, max_participants=self.max_members)}"
        )
        result_badged = empty_with_max if joined_count == 0 else t"({joined_count}/{self.max_members})"
        return t"{incognito_prefix}{result_badged}"

    @property
    def participants_list_text(self) -> FormattedText:
        """
        Textual representation of the list of participants in the meeting with one line per participant.

        If there are users in the waiting list, they are shown after the participants with a separator and a title.
        """
        participant_list = [link.participant_name for link in self.joined_links if not link.is_waiting_list]
        waiting_list = [link.participant_name for link in self.joined_links if link.is_waiting_list]

        participants_part = (
            FormattedText("\n  ").append(FormattedText.join("\n  ", participant_list))
            if participant_list
            else FormattedText("")
        )

        if waiting_list:
            waiting_header = ButtonMessages.WAITING_LIST.get(lang=self.lang)
            waiting_names = FormattedText.join("\n  ", waiting_list)
            waiting_section = (
                FormattedText(f"\n--- {Emojis.WAITING} ").append(waiting_header).append(" \n  ").append(waiting_names)
            )
            return participants_part.append(waiting_section)

        return participants_part

    @property
    def participants_text(self) -> Template:
        """
        Textual representation of the participants information of the meeting. The list of participants is not included
        for incognito meetings.

        To get the participants text ignoring whether the meeting is incognito or not,
        use `participants_text_with_list`.
        """
        participant_list: Template | FormattedText | str = t"" if self.incognito else self.participants_list_text
        return t"{self.participants_text_title}{participant_list}"

    @property
    def participants_text_title(self) -> Template:
        """
        This is the title of the participants section of the meeting.

        It includes things like the number of participants, the maximum number of participants, etc.
        """
        if len(self.joined_links) == 0:
            total_participants: FormattedText | Template = MeetingMessages.EMPTY.get(lang=self.lang)
        elif len(self.joined_links) == 1:
            total_participants = t"1 {MeetingMessages.PARTICIPANT.get(lang=self.lang)}"
        else:
            n = len(self.joined_links)
            total_participants = t"{n} {MeetingMessages.PARTICIPANTS.get(lang=self.lang)}"

        max_participants: FormattedText | Template = (
            MeetingMessages.MAX_PARTICIPANTS.get(lang=self.lang, max_participants=self.max_members)
            if self.max_members
            else t"({MeetingMessages.NO_LIMIT_PARTICIPANTS.get(lang=self.lang)})"
        )

        incognito_prefix = f"{Emojis.GLASSES} " if self.incognito else ""
        return t"{incognito_prefix}{total_participants} {max_participants}"

    @property
    def participants_text_with_list(self) -> Template:
        """
        Textual representation of the participants section of the meeting. The list of participants is always included.
        """
        return t"{self.participants_text_title}{self.participants_list_text}"

    @property
    def _datetime_section(self) -> Template:
        """Date/time section for the meeting message.

        When an end time is set, shows separate start and stop lines using ▶️/⏹️.
        Otherwise shows a single clock line with the datetime or a not-set placeholder.
        """
        if self.datetime is None:
            datetime_display = MeetingMessages.DATE_NOT_SET.get(lang=self.lang)
            return t"--- {Emojis.CLOCK} {datetime_display}\n"

        start_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), self.datetime, "DT")

        if self.end_datetime is None:
            return t"--- {Emojis.CLOCK} {start_entity}\n"

        stop_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), self.end_datetime, "DT")
        start_label = MeetingMessages.MEETING_START_TIME.get(lang=self.lang)
        stop_label = MeetingMessages.MEETING_STOP_TIME.get(lang=self.lang)
        return t"--- {Emojis.START} {start_label}: {start_entity}\n--- {Emojis.STOP} {stop_label}: {stop_entity}\n"

    @property
    def message(self) -> FormattedText:
        description = self.description or MeetingMessages.DESCRIPTION_NOT_SET.get(lang=self.lang)
        created_by = MeetingMessages.CREATED_BY.get(lang=self.lang, owner=self.owner.inline_name)
        location = self.location.description(lang=self.lang)
        datetime_section = self._datetime_section
        participants_text_with_list = self.participants_text_with_list
        return render(
            t"{Bold(self.title)} ({created_by})\n\n"
            t"--- {Emojis.DESCRIPTION} {description}\n"
            t"{datetime_section}"
            t"--- {Emojis.MAP} {location}\n"
            t"--- {Emojis.JOINED} {participants_text_with_list}"
        )

    @property
    def inline_message(self) -> FormattedText:
        """
        Similar to message but used when the meeting is shared inline.
        Properties that are not set are omitted.
        """
        created_by = MeetingMessages.CREATED_BY.get(lang=self.lang, owner=self.owner.inline_name)
        description_section: Template | str = (
            t"\n--- {Emojis.DESCRIPTION} {self.description}" if self.description else ""
        )
        location_section: Template | str = (
            "" if self.location.empty() else t"\n--- {Emojis.MAP} {self.location.description(lang=self.lang)}\n"
        )
        participants_text = self.participants_text
        if self.datetime is not None:
            datetime_section = self._datetime_section
            return render(
                t"{Bold(self.title)} ({created_by})"
                t"{description_section}"
                t"\n{datetime_section}"
                t"{location_section}"
                t"--- {Emojis.JOINED} {participants_text}"
            )
        return render(
            t"{Bold(self.title)} ({created_by})"
            t"{description_section}{location_section}"
            t"\n--- {Emojis.JOINED} {participants_text}"
        )

    @property
    def inline_query_message(self) -> FormattedText:
        """Plain-text preview shown below the title in inline query results."""
        result = t"{Emojis.JOINED} {self.participants_badge}"

        if self.datetime:
            return render(t"{result}\n{Emojis.CLOCK} {self._plain_datetime}")

        return render(result)

    def _join_leave_row(self, lang: str) -> list[ButtonConfig]:
        """Return the [JOIN, (INVITE,) LEAVE] row, inserting INVITE only when allow_invitation is True."""
        join = ButtonConfig(text=ButtonMessages.JOIN.get(lang=lang), callback_data=cb.JOIN.with_id(self.db_id))
        invite = ButtonConfig(text=ButtonMessages.INVITE.get(lang=lang), callback_data=cb.INVITE.with_id(self.db_id))
        leave = ButtonConfig(text=ButtonMessages.LEAVE.get(lang=lang), callback_data=cb.LEAVE.with_id(self.db_id))
        return [join, invite, leave] if self.allow_invitation else [join, leave]

    @property
    def main_view(self) -> MitupView:
        keyboard: Keyboard = []
        if not (self.lock_on_start and self.is_in_progress):
            keyboard.append(self._join_leave_row(self.user_language))
        keyboard.extend(
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.EDIT.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING.with_id(self.db_id),
                    ),
                    ButtonConfig(text=ButtonMessages.CHAT.get(lang=self.user_language), callback_data=cb.CHAT),
                    ButtonConfig(
                        text=ButtonMessages.DELETE.get(lang=self.user_language),
                        callback_data=cb.DELETE_MEETING.with_id(self.db_id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.SHARE.get(lang=self.user_language), switch_inline_query=str(self.db_id)
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.back(lang=self.user_language),
                        callback_data=cb.MAIN_MENU,
                    ),
                ],
            ]
        )
        view = MitupView(self.message, keyboard)
        if self.is_in_progress:
            view.with_context(MeetingMessages.MEETING_IN_PROGRESS.get(lang=self.user_language))
        return view

    @property
    def external_view(self) -> MitupView:
        """This is the view shown to users that do not own the meeting when checking through meetings I have joined"""
        keyboard: Keyboard = []
        if not (self.lock_on_start and self.is_in_progress):
            keyboard.append(self._join_leave_row(self.user_language))
        keyboard.append(
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=self.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ]
        )
        view = MitupView(self.inline_message, keyboard)
        if self.is_in_progress:
            view.with_context(MeetingMessages.MEETING_IN_PROGRESS.get(lang=self.user_language))
        return view

    def view_for(self, user: User) -> MitupView:
        """Get the appropriate view for the given user depending on whether they own the meeting or not."""
        return self.main_view if self.is_owned_by(user) else self.external_view

    @property
    def edit_view(self) -> MitupView:
        keyboard: Keyboard = [
            [
                ButtonConfig(
                    text=ButtonMessages.TITLE.get(lang=self.user_language),
                    callback_data=cb.EDIT_MEETING_TITLE.with_id(self.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DESCRIPTION.get(lang=self.user_language),
                    callback_data=cb.EDIT_MEETING_DESCRIPTION.with_id(self.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.WHEN.get(lang=self.user_language),
                    callback_data=cb.EDIT_MEETING_WHEN.with_id(self.db_id),
                ),
            ],
        ]
        keyboard.extend(
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.PARTICIPANTS.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_PARTICIPANTS.with_id(self.db_id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.LOCATION.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_LOCATION.with_id(self.db_id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.LANGUAGE.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_LANGUAGE.with_id(self.db_id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.SETTINGS.get(lang=self.user_language),
                        callback_data=cb.EDIT_MEETING_SETTINGS.with_id(self.db_id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.DONE.get(lang=self.user_language),
                        callback_data=cb.SHOW_MEETING.with_id(self.db_id),
                    ),
                ],
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.back(lang=self.user_language),
                        callback_data=cb.MAIN_MENU,
                    ),
                ],
            ]
        )
        return MitupView(self.message, keyboard)

    @property
    def settings_view(self) -> MitupView:
        keyboard = [
            [
                options_button(
                    cb.SET_MEETING_WAITING_LIST.with_id(self.db_id),
                    ButtonMessages.WAITING_LIST.get(lang=self.owner.lang),
                    self.waiting_list,
                ),
                options_button(
                    cb.SET_MEETING_PUBLIC.with_id(self.db_id),
                    ButtonMessages.PUBLIC.get(lang=self.owner.lang),
                    self.public,
                ),
            ],
            [
                options_button(
                    cb.SET_MEETING_ALLOW_INVITATIONS.with_id(self.db_id),
                    ButtonMessages.OPEN_INVITATION.get(lang=self.owner.lang),
                    self.allow_invitation,
                ),
                options_button(
                    cb.SET_MEETING_INCOGNITO.with_id(self.db_id),
                    ButtonMessages.INCOGNITO.get(lang=self.owner.lang),
                    self.incognito,
                ),
            ],
        ]

        return MitupView(
            MeetingMessages.EDIT_SETTINGS_MESSAGE.get(lang=self.owner.lang),
            keyboard=keyboard,
        ).with_back_button(ButtonMessages.EDIT, self.owner.lang, cb.EDIT_MEETING.with_id(self.db_id))

    def _when_view_with_start(self) -> tuple[str | FormattedText, Keyboard]:
        start_entity = EntityDateTime(MeetingMessages.MEETING_TIME.get_text(), cast(dt.datetime, self.datetime), "DT")
        set_start_button = ButtonConfig(
            text=ButtonMessages.SET_START_TIME.get(lang=self.user_language),
            callback_data=cb.SET_MEETING_START_TIME.with_id(self.db_id),
        )
        set_end_button = ButtonConfig(
            text=ButtonMessages.SET_END_TIME.get(lang=self.user_language),
            callback_data=cb.SET_MEETING_END_TIME.with_id(self.db_id),
        )
        lock_toggle = options_button(
            cb.SET_MEETING_LOCK_ON_START.with_id(self.db_id),
            ButtonMessages.LOCK_ON_START.get(lang=self.user_language),
            self.lock_on_start,
        )
        clear_button = ButtonConfig(
            text=ButtonMessages.CLEAR_TIMES.get(lang=self.user_language),
            callback_data=cb.DELETE_MEETING_TIMES.with_id(self.db_id),
        )

        if self.end_datetime is not None:
            end_entity = EntityDateTime(
                MeetingMessages.MEETING_TIME.get_text(), cast(dt.datetime, self.end_datetime), "DT"
            )
            description: str | FormattedText = MeetingMessages.WHEN_VIEW_BOTH.get(
                lang=self.user_language,
                start_datetime=render(t"{start_entity}"),
                end_datetime=render(t"{end_entity}"),
            )
        else:
            description = MeetingMessages.WHEN_VIEW_START_ONLY.get(
                lang=self.user_language,
                start_datetime=render(t"{start_entity}"),
            )

        keyboard: Keyboard = [
            [set_start_button, set_end_button],
            [lock_toggle],
            [clear_button],
        ]
        return description, keyboard

    @property
    def when_view(self) -> MitupView:
        if self.datetime is None:
            description: str | FormattedText = MeetingMessages.WHEN_VIEW_NO_TIMES.get(lang=self.user_language)
            keyboard: Keyboard = [
                [
                    ButtonConfig(
                        text=ButtonMessages.SET_START_TIME.get(lang=self.user_language),
                        callback_data=cb.SET_MEETING_START_TIME.with_id(self.db_id),
                    ),
                ],
                [
                    options_button(
                        cb.SET_MEETING_LOCK_ON_START.with_id(self.db_id),
                        ButtonMessages.LOCK_ON_START.get(lang=self.user_language),
                        self.lock_on_start,
                    ),
                ],
            ]
        else:
            description, keyboard = self._when_view_with_start()

        return MitupView(description, keyboard).with_back_button(
            ButtonMessages.EDIT, self.user_language, cb.EDIT_MEETING.with_id(self.db_id)
        )

    def enforce_datetime_ordering(self) -> bool:
        """Clear end_datetime if it's no longer after datetime. Returns True if cleared."""
        if self.end_datetime is None or self.datetime is None:
            return False
        start = self.datetime.replace(tzinfo=dt.UTC) if self.datetime.tzinfo is None else self.datetime
        end = self.end_datetime.replace(tzinfo=dt.UTC) if self.end_datetime.tzinfo is None else self.end_datetime
        if start >= end:
            self.end_datetime = None
            self.lock_on_start = False
            return True
        return False

    def inline_view(self, *, chat_instance: str | None = None) -> MitupInlineView:
        is_searchable = chat_instance is not None
        footnote = (
            MeetingMessages.SEARCHABLE_FOOTNOTE.get(lang=self.lang)
            if is_searchable
            else MeetingMessages.NOT_SEARCHABLE_FOOTNOTE.get(lang=self.lang)
        )
        view = MitupInlineView(
            description=self.inline_message,
            keyboard=self.build_inline_keyboard(
                is_searchable=is_searchable,
                is_locked_and_in_progress=self.lock_on_start and self.is_in_progress,
            ),
            id=str(self.db_id),
            title=str(self.title),
            inline_description=self.inline_query_message,
        )
        if self.is_in_progress:
            view.with_context(MeetingMessages.MEETING_IN_PROGRESS.get(lang=self.lang))
        return view.with_footnote(footnote)

    def build_inline_keyboard(
        self, *, is_searchable: bool = False, is_locked_and_in_progress: bool = False
    ) -> Keyboard:
        keyboard: Keyboard = []

        if not is_locked_and_in_progress:
            keyboard.append(self._join_leave_row(self.lang))

        if self.public:
            keyboard.append(
                [
                    ButtonConfig(text=ButtonMessages.SHARE.get(lang=self.lang), switch_inline_query=str(self.db_id)),
                ]
            )

        if not is_searchable:
            keyboard.append(
                [
                    ButtonConfig(
                        text=ButtonMessages.MAKE_SEARCHABLE.get(lang=self.lang),
                        callback_data=cb.ATTACH_TO_CHAT.with_id(self.db_id),
                    ),
                ]
            )

        return keyboard

    @overload
    @classmethod
    def by_id(
        cls, session: Session, meetup_id: int, must_exist: Literal[True], include_inactive: bool = False
    ) -> Self: ...  # pragma: no cover

    @overload
    @classmethod
    def by_id(
        cls, session: Session, meetup_id: int, must_exist: bool = ..., include_inactive: bool = False
    ) -> Self | None: ...  # pragma: no cover

    @classmethod
    def by_id(
        cls,
        session: Session,
        meetup_id: int,
        must_exist: bool = False,
        include_inactive: bool = True,
    ) -> Self | None:
        statement = select(cls).where(cls.id == meetup_id)
        if (found_meetup := session.exec(statement).first()) is not None and (found_meetup.active or include_inactive):
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
