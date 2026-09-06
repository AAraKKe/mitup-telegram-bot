"""Inline-keyboard schema persisted as message JSON.

`MessageButtons` stores a `Keyboard` in a database JSON column, so these models are a
persisted wire format: any change to field names, defaults, or serializers changes what
is stored. They stay pure data — this module must not import views, models, or db, and
rendering to Telegram markup lives in the view layer.
"""

from typing import Literal, Self

from pydantic import BaseModel, field_validator, model_validator

from mitup_bot.callback_data import CallbackData

# The accent a rendered button may carry. The names are Telegram's rich-button styles; the
# schema stores them as plain strings so the wire format stays free of telegram imports.
ButtonStyle = Literal["primary", "success", "danger", "link"]


class ButtonConfig(BaseModel):
    text: str
    callback_data: CallbackData | str | None = None
    switch_inline_query: str | None = None
    switch_inline_query_current_chat: str | None = None
    # An https:// or tg:// deep link. Used by buttons that open an external page (e.g. the Patreon
    # OAuth consent screen), which produce no callback query and so carry no callback_data.
    url: str | None = None
    # None renders with Telegram's default button look.
    style: ButtonStyle | None = None
    # An inert button: drawn like the others but answering no tap, so it names no action.
    disabled: bool = False

    @field_validator("callback_data")
    @classmethod
    def validate_callback_data(cls, value: CallbackData | str | None) -> CallbackData | str | None:
        str_value = str(value)
        if len(str_value.encode("utf-8")) > 64:
            raise ValueError(f"The callback_data {str_value!r} is bigger than the 64B allowed by Telegram")
        return value

    @model_validator(mode="after")
    def validate_exactly_one_action(self) -> Self:
        action_count = sum(
            action_field is not None
            for action_field in [
                self.callback_data,
                self.url,
                self.switch_inline_query,
                self.switch_inline_query_current_chat,
            ]
        )
        if self.disabled:
            if action_count:
                raise ValueError(
                    "A disabled button answers no tap, so callback_data, switch_inline_query, "
                    "switch_inline_query_current_chat and url must all be unset"
                )
            return self
        if action_count != 1:
            raise ValueError(
                "Exactly one of callback_data, switch_inline_query, switch_inline_query_current_chat, "
                "or url must be set"
            )
        return self


ButtonRow = list[ButtonConfig]
Keyboard = list[ButtonRow]
