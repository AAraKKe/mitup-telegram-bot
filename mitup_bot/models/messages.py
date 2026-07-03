from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel
from telegram import Update

from mitup_bot.exceptions import NoMessageAvailable
from mitup_bot.views import Keyboard

from .base_model import BaseModel
from .mutable_model import MutableModel

if TYPE_CHECKING:  # pragma: no cover
    from . import Meetup, User


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
    def from_update(cls, update: Update, meeting: Meetup, user: User) -> Message:
        message_id = None
        inline_message_id = None
        chat_instance = None
        keyboard = meeting.inline_view().keyboard
        if update.effective_message:
            message_id = update.effective_message.message_id
            # This is a message from the chat with the bot. Either by the user that owns it
            # or someone that has joined. Only shows the edit button if the user owns it
            keyboard = (
                meeting.main_view().keyboard if user.own_meeting(meeting.db_id) else meeting.external_view().keyboard
            )
        if update.callback_query and update.callback_query.inline_message_id:
            inline_message_id = update.callback_query.inline_message_id
            chat_instance = update.callback_query.chat_instance
            # This is a message from a shared meeting outside the chat with the bot
            # Rebuild the keyboard with the now-known chat_instance
            keyboard = meeting.inline_view(chat_instance=chat_instance).keyboard
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
