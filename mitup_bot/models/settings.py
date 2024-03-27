import datetime as dt
import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:  # pragma: no cover
    from .users import User


class Settings(SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    updated_time: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    languaje: str = "en"
    timezone: str = "UTC"
    notification: bool = True
    notification_time: int = 5
    default_extension_period: int = 0
    default_waiting_list: bool = True
    default_public: bool = True
    default_allow_invitation: bool = True
    default_show_members: bool = True
    default_show_timezone: bool = True

    user: "User" = Relationship(back_populates="settings")

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            # While we implement proper timezone handling, users can set random timezones.
            # We should log this and use UTC instead.
            logging.warning(f"Invalid timezone {self.timezone} by user {self.user_id}. Using UTC instead.")
            return ZoneInfo("UTC")
