import datetime as dt
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from .users import User


class Settings(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime = Field(default_factory=dt.datetime.utcnow)
    updated_time: dt.datetime = Field(default_factory=dt.datetime.utcnow)
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
