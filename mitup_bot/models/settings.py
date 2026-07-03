import datetime as dt
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import Column, DateTime, FetchedValue
from sqlmodel import Field, Relationship, SQLModel

from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, SettingsMessages
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button

from .base_model import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from .users import User

log = structlog.get_logger(__name__)


class Settings(BaseModel, SQLModel, table=True):
    __tablename__: str = "settings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
    language: str = TranslationEngine.FALLBACK_LANG
    timezone: str = "UTC"
    notification: bool = True
    notification_time: int = 5
    timeout: int = 5
    default_waiting_list: bool = False
    default_public: bool = False
    default_allow_invitation: bool = False
    default_incognito: bool = False
    default_lock_on_start: bool = False

    # Deliberately lazy: no code path traverses `settings.user` (settings are always reached
    # through the user), so it never triggers a load under the async engine.
    user: User = Relationship(back_populates="settings")

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
            log.warning("Invalid timezone, falling back to UTC", timezone=self.timezone, user_id=self.user_id)
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
                    cb.SET_DEFAULT_LOCK_ON_START,
                    ButtonMessages.LOCK_ON_START.get(lang=self.language),
                    self.default_lock_on_start,
                ),
            ],
        ]

        return MitupView(
            SettingsMessages.DEFAULT_OPTIONS_DESCRIPTION.get(lang=self.language),
            keyboard=keyboard,
        ).with_back_button(ButtonMessages.SETTINGS, self.language, cb.SETTINGS)
