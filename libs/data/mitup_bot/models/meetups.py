import datetime as dt
from typing import TYPE_CHECKING, ClassVar, Literal, Self, cast, overload
from zoneinfo import ZoneInfo

from pydantic.config import ConfigDict
from sqlalchemy import JSON, BigInteger, Column, DateTime, FetchedValue
from sqlmodel import Field, Relationship, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import limits
from mitup_bot.exceptions import MeetupNotFound
from mitup_bot.keyboards import Keyboard
from mitup_bot.models import Message

from .base_model import BaseModel
from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from telegram import Update

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

    def empty(self) -> bool:
        return self.coerced_name is None and self.coordinates is None


class Meetup(BaseModel, SQLModel, table=True):
    __tablename__: str = "meetups"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    owner_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE", sa_type=BigInteger)
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

    # lazy="selectin" on all three: model properties traverse them in plain Python
    # (`lang`/`timezone` via owner, `message_from_update` via messages, participant counts and
    # lists via joined_links), and implicit lazy loads raise MissingGreenlet under the async
    # engine.
    owner: User = Relationship(back_populates="meetups", sa_relationship_kwargs={"lazy": "selectin"})
    messages: list[Message] = Relationship(back_populates="meetup", sa_relationship_kwargs={"lazy": "selectin"})
    joined_links: list[JoinedUsers] = Relationship(
        back_populates="meetup",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

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
    def effective_max_members(self) -> int | None:
        """`max_members` tightened by the owner's participant cap, or None (unlimited) which is
        only reachable for uncapped (Patron/Organizer) owners.

        Every capacity read — `full`, waiting-list promotion, and the capacity displays — resolves
        through this, so a capped owner's meeting never exceeds the cap whether they set a higher
        limit or none at all. A meeting already over the cap (owner dropped a tier, or pre-cap data)
        keeps its participants and simply reads as full until it drops back under the cap.
        """
        return limits.effective_participant_capacity(self.owner, self.max_members)

    @property
    def full(self) -> bool:
        cap = self.effective_max_members
        return False if cap is None else self.n_participants >= cap

    @property
    def is_in_progress(self) -> bool:
        """Return True when the current UTC time falls within the meeting's in-progress window.

        With a start and an end time the window is bounded to `[start, end)`; with a start time but
        no end time it is open-ended, in progress from `start` onward. Without a start time, or once
        the meeting is deactivated, there is no window at all.
        """
        # Business rule: an inactive meeting is never in progress. This intrinsically ends the
        # open-ended window at deactivation, so callers need no separate activeness check.
        if not self.active:
            return False
        if self.datetime is None:
            return False
        now = dt.datetime.now(dt.UTC)
        start = self.datetime if self.datetime.tzinfo else self.datetime.replace(tzinfo=dt.UTC)
        if now < start:
            return False
        if self.end_datetime is None:
            return True
        end = self.end_datetime if self.end_datetime.tzinfo else self.end_datetime.replace(tzinfo=dt.UTC)
        return now < end

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
            cap = self.effective_max_members
            to_promote = self.n_waiting if cap is None else min(self.n_waiting, cap - self.n_participants)
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
        # id breaks created_time ties in insert order, so promotion order stays deterministic when
        # rows share a timestamp.
        return sorted(
            (link for link in self.joined_links if link.is_waiting_list),
            key=lambda x: (cast(dt.datetime, x.created_time), x.id or 0),
        )

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

    def add_message(self, update: Update, keyboard: Keyboard) -> Message:
        """Link a message to this meeting if not already linked and return the message object.

        `keyboard` is stored on a newly created message; build it with
        `views.meeting.keyboard_for_update`.
        """
        if (message := self.message_from_update(update)) is None:
            message = Message.from_update(update, self, keyboard)
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

    @overload
    @classmethod
    async def by_id(
        cls,
        session: AsyncSession,
        meetup_id: int,
        must_exist: Literal[True],
        include_inactive: bool = True,
        *,
        for_update: bool = False,
    ) -> Self: ...  # pragma: no cover

    @overload
    @classmethod
    async def by_id(
        cls,
        session: AsyncSession,
        meetup_id: int,
        must_exist: bool = ...,
        include_inactive: bool = True,
        *,
        for_update: bool = False,
    ) -> Self | None: ...  # pragma: no cover

    @classmethod
    async def by_id(
        cls,
        session: AsyncSession,
        meetup_id: int,
        must_exist: bool = False,
        include_inactive: bool = True,
        *,
        for_update: bool = False,
    ) -> Self | None:
        statement = select(cls).where(cls.id == meetup_id)
        if for_update:
            # The meetups row is the per-meeting mutex: every participant-mutating path locks it
            # here before reading capacity or waiting-list state, so cross-user races (double join
            # on the last slot, leave-with-promotion vs join) serialize on this row. Lock ordering:
            # meeting row first, then anything else; never lock two meetings in one transaction.
            # populate_existing is load-bearing: the current-user eager loads usually pulled this
            # meetup and its joined_links into the identity map already, and without it the locked
            # SELECT would return the stale pre-lock state instead of re-reading it. FOR UPDATE
            # applies only to the meetups row — the selectin follow-ups run unlocked, which is
            # fine because the row lock itself is what serializes writers.
            # Footgun: populate_existing re-hydrates EVERY entity this statement pulls in,
            # including identity-mapped Users reached through owner/joined_links — which resets
            # their lazy="raise" collections (User.meetups/joined_links) to unloaded. Callers
            # holding an already-loaded User must re-load those collections after this call:
            # `await session.refresh(user, ["meetups", "joined_links"])`.
            statement = statement.with_for_update().execution_options(populate_existing=True)
        found_meetup = (await session.exec(statement)).first()
        if found_meetup is not None and (found_meetup.active or include_inactive):
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
