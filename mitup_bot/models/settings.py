import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Column, func
from sqlmodel import Field, Relationship, SQLModel

from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from .users import User


class Settings(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = Field(default=None, foreign_key="users.id")
    created_time: dt.datetime | None = Field(sa_column=Column(TIMESTAMP, server_default=func.now()))
    updated_time: dt.datetime | None = Field(sa_column=Column(TIMESTAMP, server_default=func.now()))
    languaje: str | None = Field(default="en")
    timezone: str
    notification: bool | None = Field(default=True)
    notification_time: int | None = Field(default=5)
    default_extension_period: int | None = Field(default=60)
    default_waiting_list: bool | None = Field(default=True)
    default_public: bool | None = Field(default=True)
    default_allow_invitation: bool | None = Field(default=True)
    default_show_members: bool | None = Field(default=True)
    default_show_timezone: bool | None = Field(default=True)

    user: "User" = Relationship(back_populates="settings")
