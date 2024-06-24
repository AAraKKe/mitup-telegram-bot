from enum import auto

from mitup_bot.callback_id import CallbackId


class InlineQueryId(CallbackId):
    SHARE_MEETING = auto()
