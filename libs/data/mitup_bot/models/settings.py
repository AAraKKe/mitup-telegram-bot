import datetime as dt
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, FetchedValue
from sqlmodel import Field, Relationship, SQLModel
from sqlmodel.main import SQLModelConfig

from mitup_bot.limits import MEETING_MAX_TIMEOUT_MINUTES
from mitup_bot.translations import TranslationEngine

from .base_model import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from .users import User

log = structlog.get_logger(__name__)

SETTINGS_TIMEOUT_MAX_CONSTRAINT = "ck_settings_timeout_max"


class Settings(BaseModel, SQLModel, table=True):
    __tablename__: str = "settings"
    __table_args__ = (
        CheckConstraint(f"timeout <= {MEETING_MAX_TIMEOUT_MINUTES}", name=SETTINGS_TIMEOUT_MAX_CONSTRAINT),
    )
    # Validate on assignment so the `timeout` ceiling holds on every write path, not just the
    # settings flow. Loading is unaffected — SQLAlchemy populates instance state directly, so a row
    # that is out of range still reads back.
    model_config = SQLModelConfig(validate_assignment=True)

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE", sa_type=BigInteger)
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
    language: str = TranslationEngine.FALLBACK_LANG
    timezone: str = "UTC"
    notification: bool = True
    notification_time: int = 5
    timeout: int = Field(default=5, le=MEETING_MAX_TIMEOUT_MINUTES, sa_type=BigInteger)
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
