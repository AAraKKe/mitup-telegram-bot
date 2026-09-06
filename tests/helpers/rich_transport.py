"""Readers for the raw Bot API calls the outbound transport makes.

Message bodies go out through `Bot.do_api_request`, so a mocked bot records one opaque call per
send or edit instead of a signature whose parameters a test can name. These wrap that call in
something assertions can read a chat, a target or a rendered body off.
"""

import re
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

from telegram import InputFile

BUTTON_ROW_RE = re.compile(r"<tg-button-row>.*?</tg-button-row>", re.DOTALL)
# The keyboard closing a message, together with the rule drawn above it. Both are appended after
# the body, so taking this off the end is what leaves the body a screen composed.
CLOSING_KEYBOARD_RE = re.compile(r"(?:<hr/>)?(?:<tg-button-row>.*?</tg-button-row>)+$", re.DOTALL)


@dataclass(frozen=True)
class RichCall:
    """One raw call: the endpoint it addressed and the parameters it carried."""

    endpoint: str
    api_kwargs: dict[str, Any]

    @property
    def html(self) -> str:
        """The whole rendered message: the body followed by its button rows."""
        return self.api_kwargs["rich_message"]["html"]

    @property
    def body_html(self) -> str:
        """The message content, with the keyboard closing it and that keyboard's rule taken off.

        A screen may carry button rows inside its body too, so this cuts the closing keyboard off
        the end rather than at the first row it finds.
        """
        return CLOSING_KEYBOARD_RE.sub("", self.html)

    @property
    def button_rows(self) -> list[str]:
        """The button-row markup closing the content, one entry per keyboard row."""
        return BUTTON_ROW_RE.findall(self.html)

    @property
    def media(self) -> list[dict[str, Any]]:
        """The media entries the content references, one per file the message carries."""
        return self.api_kwargs["rich_message"].get("media", [])

    @property
    def uploads(self) -> dict[str, InputFile]:
        """The files travelling beside the message, keyed by the name `attach://` gives each one."""
        return {name: value for name, value in self.api_kwargs.items() if isinstance(value, InputFile)}

    @property
    def reply_markup(self) -> dict[str, Any] | None:
        """The classic keyboard riding beside the content, set on inline-addressed calls."""
        return self.api_kwargs.get("reply_markup")

    @property
    def chat_id(self) -> Any:
        return self.api_kwargs.get("chat_id")

    @property
    def message_id(self) -> Any:
        return self.api_kwargs.get("message_id")

    @property
    def inline_message_id(self) -> Any:
        return self.api_kwargs.get("inline_message_id")


def rich_calls(bot: AsyncMock) -> list[RichCall]:
    """Every raw call the bot recorded, in the order they were made."""
    return [RichCall(call.args[0], call.kwargs["api_kwargs"]) for call in bot.do_api_request.call_args_list]


def rich_call(bot: AsyncMock, index: int = 0) -> RichCall:
    """One raw call by position, defaulting to the first."""
    return rich_calls(bot)[index]


def only_rich_call(bot: AsyncMock) -> RichCall:
    """The single raw call the bot recorded, asserting there was exactly one."""
    bot.do_api_request.assert_awaited_once()
    return rich_call(bot)
