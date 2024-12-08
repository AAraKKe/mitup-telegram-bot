import datetime as dt
import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Field, Relationship, SQLModel

from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button

if TYPE_CHECKING:  # pragma: no cover
    from .users import User


class Settings(SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    updated_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    language: str = TranslationEngine.FALLBACK_LANG
    timezone: str = "UTC"
    notification: bool = True
    notification_time: int = 5
    timeout: int = 5
    default_waiting_list: bool = False
    default_public: bool = False
    default_allow_invitation: bool = False
    default_incognito: bool = False
    default_show_timezone: bool = True

    user: "User" = Relationship(back_populates="settings")

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, Settings) else NotImplemented

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            # While we implement proper timezone handling, users can set random timezones.
            # We should log this and use UTC instead.
            logging.warning(f"Invalid timezone {self.timezone} by user {self.user_id}. Using UTC instead.")
            return ZoneInfo("UTC")

    def default_meeting_settings_view(self) -> MitupView:
        keyboard = [
            [
                options_button(
                    cb.SET_DEFAULT_WAITING_LIST,
                    ButtonMessages.WAITING_LIST.get(lang=self.language),
                    self.default_waiting_list,
                ),
                options_button(
                    cb.SET_DEFAULT_PUBLIC,
                    ButtonMessages.PUBLIC.get(lang=self.language),
                    self.default_public,
                ),
            ],
            [
                options_button(
                    cb.SET_DEFAULT_INVITATIONS,
                    ButtonMessages.OPEN_INVITATION.get(lang=self.language),
                    self.default_allow_invitation,
                ),
                options_button(
                    cb.SET_DEFAULT_INCOGNITO,
                    ButtonMessages.INCOGNITO.get(lang=self.language),
                    self.default_incognito,
                ),
            ],
            [
                options_button(
                    cb.SET_DEFAULT_SHOW_TIMEZONE,
                    ButtonMessages.SHOW_TIMEZONE.get(lang=self.language),
                    self.default_show_timezone,
                ),
            ],
        ]

        return MitupView(
            SettingsMessages.DEFAULT_MEETING_OPTIONS_MESSAGE.get(lang=self.language),
            keyboard=keyboard,
        ).with_back_button(ButtonMessages.SETTINGS, self.language, cb.SETTINGS)
