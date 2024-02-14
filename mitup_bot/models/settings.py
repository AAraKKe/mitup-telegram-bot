import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Column
from sqlmodel import Field, Relationship, SQLModel

from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from .users import User


class Settings(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime = Field(default_factory=dt.datetime.utcnow, sa_column=Column(TIMESTAMP))
    updated_time: dt.datetime = Field(default_factory=dt.datetime.utcnow, sa_column=Column(TIMESTAMP))
    languaje: str = Field(default="en")
    timezone: str = Field(default="UTC")
    notification: bool = Field(default=True)
    notification_time: int = Field(default=5)
    default_extension_period: int = Field(default=60)
    default_waiting_list: bool = Field(default=True)
    default_public: bool = Field(default=True)
    default_allow_invitation: bool = Field(default=True)
    default_show_members: bool = Field(default=True)
    default_show_timezone: bool = Field(default=True)

    user: "User" = Relationship(back_populates="settings")
