import datetime as dt
from collections.abc import Sequence

import pytest
from telegram import Chat, Document, Location, Message, PhotoSize, Sticker

from mitup_bot.handlers.utils import message_kind

CHAT = Chat(id=1, type=Chat.PRIVATE)
DATE = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def make_message(
    *,
    text: str | None = None,
    photo: Sequence[PhotoSize] | None = None,
    sticker: Sticker | None = None,
    location: Location | None = None,
    document: Document | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=DATE,
        chat=CHAT,
        text=text,
        photo=photo,
        sticker=sticker,
        location=location,
        document=document,
    )


@pytest.mark.parametrize(
    "message, expected",
    [
        (None, "unknown"),
        (make_message(text="Madrid"), "text"),
        (make_message(), "empty"),
        (
            make_message(photo=(PhotoSize("small", "small-unique", 90, 90), PhotoSize("big", "big-unique", 800, 800))),
            "photo",
        ),
        (make_message(sticker=Sticker("s", "s-unique", 512, 512, False, False, Sticker.REGULAR)), "sticker"),
        (make_message(location=Location(longitude=-3.7, latitude=40.4)), "location"),
        (make_message(document=Document("d", "d-unique")), "document"),
    ],
    ids=["no_message", "text", "empty", "photo", "sticker", "location", "document"],
)
def test_message_kind_names_what_was_sent(message: Message | None, expected: str):
    """A photo arrives as a tuple of sizes, so it is named apart from the single-object attachments."""
    assert message_kind(message) == expected
