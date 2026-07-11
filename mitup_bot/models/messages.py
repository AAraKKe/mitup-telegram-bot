from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel

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
    message_id: int | None = None
    chat_id: int | None = None
    inline_message_id: str | None = None
    chat_instance: str | None = None
    meetup_id: int = Field(default=None, foreign_key="meetups.id")
    buttons: MessageButtons = Field(
        default=MessageButtons(keyboard=[]),
        sa_column=Column(type_=MessageButtons.as_mutable(JSON(none_as_null=True)), nullable=True),
    )

    # lazy="selectin": the inline search handler reads `message.meetup` in plain Python, and
    # implicit lazy loads raise MissingGreenlet under the async engine.
    meetup: Meetup = Relationship(back_populates="messages", sa_relationship_kwargs={"lazy": "selectin"})

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, Message) else NotImplemented

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
