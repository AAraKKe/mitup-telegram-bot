from typing import TYPE_CHECKING, cast

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel
from telegram import Update

from mitup_bot.views import Keyboard

from .mutable_model import MutableModel

if TYPE_CHECKING:
    from . import Meetup, User


class MessageButtons(MutableModel):
    keyboard: Keyboard


class Message(SQLModel, table=True):
    __tablename__ = "messages"  # type: ignore

    id: int = Field(default=None, primary_key=True)
    message_id: int | None = None
    chat_id: int | None = None
    inline_message_id: str | None = None
    meetup_id: int = Field(default=None, foreign_key="meetups.id")
    buttons: MessageButtons = Field(
        default=MessageButtons(keyboard=[]),
        sa_column=Column(type_=MessageButtons.as_mutable(JSON(none_as_null=True)), nullable=True),
    )

    meetups: "Meetup" = Relationship(back_populates="messages")

    @classmethod
    def from_update(cls, update: Update, meeting: "Meetup", user: "User") -> "Message":
        message_id = None
        inline_message_id = None
        chat_id = None
        keyboard = meeting.inline_view.keyboard
        if update.effective_message:
            message_id = update.effective_message.message_id
            # This is a message from the chat with the bot. Either by the user that owns it
            # or someone that has joined. Only shows the edit button if the user owns it
            if user.own_meeting(cast(int, meeting.id)):
                keyboard = meeting.main_view.keyboard
            else:
                keyboard = meeting.external_view.keyboard
        if update.callback_query and update.callback_query.inline_message_id:
            inline_message_id = update.callback_query.inline_message_id
            # This is a message from a shared meeting outside the chat with the bot
            # The keyboard must be a simple inline keyboard. Leave the default
        if update.effective_chat:
            chat_id = update.effective_chat.id

        return cls.model_validate(
            {
                "message_id": message_id,
                "inline_message_id": inline_message_id,
                "chat_id": chat_id,
                "meetup_id": meeting.id,
                "buttons": MessageButtons(keyboard=keyboard),
            }
        )
