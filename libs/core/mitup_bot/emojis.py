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
    SHARE = "📨"
    RED_CIRCLE = "🔴"
    NEW_MEETING = "➕"
    LIST = "📂"
    PAST = "💾"
    JOINED = "👥"
    SETTINGS = "⚙️"
    TOOLS = "🛠️"
    HELP = "❓"
    BOOK = "📖"
    MAIL = "✉️"
    HEART = "♥"
    DONATE = "💶"
    # Per-tier supporter badges: coffee, dice, and trophy.
    HOST_1 = "☕️"
    HOST_2 = "🎲"
    HOST_3 = "🏆"
    REPLY = "▶"
    REPLY_ALL = "⏩"
    ACTIVATE = "🔄"
    TIME = "🌐"
    ATTACH = "⬇"
    DEATTACH = "⬆"
    SEARCH = "🔍"
    HOURGLASS = "⌛"
    NOTIF = "⏰"
    LANG = "🔣"
    WAITING = "😴"
    PUBLIC = "🌎"
    SHIELD = "🛡️"
    FRIEND = "😄"
    GLASSES = "🕶"
    CLIP = "📎"
    PACKAGE = "📦"
    PHONE = "📱"
    THINK = "🤔"
    UFO = "🛸"
    BRAIN = "🧠"
    ROCKET = "🚀"
    START = "▶️"
    STOP = "⏹️"

    def __str__(self):
        return self.value

    @classmethod
    def boolean(cls, value: bool) -> str:
        return cls.CHECK.value if value else cls.RED_CIRCLE.value
