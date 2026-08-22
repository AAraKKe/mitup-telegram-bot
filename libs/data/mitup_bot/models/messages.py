from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, Column
from sqlmodel import Field, Relationship, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.exceptions import NoMessageAvailable
from mitup_bot.keyboards import Keyboard

from .base_model import BaseModel
from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from telegram import Update

    from . import Meetup


class MessageButtons(MutableModel):
    keyboard: Keyboard


class Message(BaseModel, SQLModel, table=True):
    __tablename__ = "messages"

    id: int = Field(default=None, primary_key=True)
    message_id: int | None = Field(default=None, sa_type=BigInteger)
    chat_id: int | None = Field(default=None, sa_type=BigInteger)
    inline_message_id: str | None = None
    chat_instance: str | None = None
    meetup_id: int = Field(default=None, foreign_key="meetups.id")
    buttons: MessageButtons = Field(
        default=MessageButtons(keyboard=[]),
        sa_column=Column(type_=MessageButtons.as_mutable(JSON(none_as_null=True)), nullable=True),
    )
    # Fingerprint of the payload Telegram last confirmed for this card, written by the write
    # lifecycle's reconcile once an edit is delivered. NULL means nothing has been confirmed yet,
    # so the next render always edits.
    render_digest: str | None = None

    # lazy="selectin": the inline search handler reads `message.meetup` in plain Python, and
    # implicit lazy loads raise MissingGreenlet under the async engine.
    meetup: Meetup = Relationship(back_populates="messages", sa_relationship_kwargs={"lazy": "selectin"})

    def __hash__(self) -> int:
        # `render_digest` is delivery bookkeeping rather than part of what identifies a card: two
        # rows describing the same card differ by it as soon as one of them has been delivered.
        return hash(self.model_dump_json(exclude={"id", "render_digest"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, Message) else NotImplemented

    @classmethod
    async def meetup_id_for_inline_message(cls, session: AsyncSession, inline_message_id: str) -> int | None:
        """Return the meeting this shared (inline) message is tracked for, or None when untracked.

        `inline_message_id` is globally unique, so the lookup needs no further scoping.
        """
        statement = select(cls).where(cls.inline_message_id == inline_message_id)
        message = (await session.exec(statement)).first()
        return message.meetup_id if message else None

    @classmethod
    def for_shared_card(cls, inline_message_id: str, meeting: Meetup, keyboard: Keyboard) -> Message:
        """Create the tracked message for a meeting card sent through inline mode.

        A card is addressed by `inline_message_id` alone; the chat it landed in stays unknown until
        somebody interacts with it, which is what `capture_chat_instance` is for.

        Keyboard selection is view-layer work — build it with `views.meeting.inline_view`.
        """
        return cls.model_validate(
            {
                "inline_message_id": inline_message_id,
                "meetup_id": meeting.db_id,
                "buttons": MessageButtons(keyboard=keyboard),
            }
        )

    def capture_chat_instance(self, update: Update) -> None:
        """Store the chat a shared card lives in, which only an interaction with the card reveals.

        Mirrors `from_update`: the chat instance is stored for inline (shared) messages only, since
        that is what makes a meeting resolvable by `search_chat_meetings`.
        """
        query = update.callback_query
        if self.chat_instance is None and query is not None and query.inline_message_id is not None:
            self.chat_instance = query.chat_instance

    @classmethod
    def from_update(cls, update: Update, meeting: Meetup, keyboard: Keyboard) -> Message:
        """Create the stored message for the update, persisting `keyboard` as its buttons.

        Keyboard selection is view-layer work — build it with `views.meeting.keyboard_for_update`.
        """
        message_id = None
        inline_message_id = None
        chat_instance = None
        if update.effective_message:
            message_id = update.effective_message.message_id
        if update.callback_query and update.callback_query.inline_message_id:
            inline_message_id = update.callback_query.inline_message_id
            chat_instance = update.callback_query.chat_instance
        chat_id = update.effective_chat.id if update.effective_chat else None
        if message_id is None and inline_message_id is None:
            raise NoMessageAvailable("No message_id or inline_message_id found in the update")

        return cls.model_validate(
            {
                "message_id": message_id,
                "inline_message_id": inline_message_id,
                "chat_id": chat_id,
                "chat_instance": chat_instance,
                "meetup_id": meeting.db_id,
                "buttons": MessageButtons(keyboard=keyboard),
            }
        )
