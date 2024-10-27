from enum import Enum


class Emojis(Enum):
    # FLAGS
    FLAG_BR = "🇧🇷"
    FLAG_ES = "🇪🇸"
    FLAG_US = "🇺🇸"
    FLAG_DE = "🇩🇪"
    FLAG_IT = "🇮🇹"

    # Symbols
    CLOCK = "🕒"
    PROHIB = "🚫"
    MAP = "🗺️"
    PEOPLE = "👥"
    DESCRIPTION = "📄"
    USER_WAITING = "📝"
    LOCK = "🔒"
    CALENDAR = "🗓️"
    TITLE = "🅰️"
    PIN = "📍"
    HOME = "🏠"
    CHECK = "✅"
    CANCEL = "❌"
    DELETE = "🗑️"
    EDIT = "✏️"
    CHAT = "💬"
    SHARE = "📨"
    RED_CIRCLE = "🔴"
    NEW_MEETING = "➕"
    LIST = "📂"
    PAST = "💾"
    JOINED = "👥"
    SETTINGS = "⚙️"
    HELP = "❓"
    HEART = "♥"
    DONATE = "💶"
    REPLY = "▶"
    REPLY_ALL = "⏩"
    ACTIVATE = "🔄"
    TIME = "🌐"
    ATTACH = "⬇"
    DEATTACH = "⬆"
    HOURGLASS = "⌛"
    NOTIF = "⏰"
    LANG = "🔣"
    WAITING = "😴"
    PUBLIC = "🌎"
    SHIELD = "🛡️"
    FRIEND = "😄"
    GLASSES = "🕶"
    CLIP = "📎"
    PHONE = "📱"
    THINK = "🤔"
    UFO = "🛸"
    BRAIN = "🧠"

    def __str__(self):
        return self.value

    @classmethod
    def boolean(cls, value: bool) -> str:
        return cls.CHECK.value if value else cls.RED_CIRCLE.value
