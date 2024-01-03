import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlalchemy import TIMESTAMP, Column, func
from sqlmodel import Field, SQLModel, Relationship

from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from .users import User


class Settings(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_time: Optional[dt.datetime] = Field(
        sa_column=Column(TIMESTAMP, server_default=func.now())
    )
    updated_time: Optional[dt.datetime] = Field(
        sa_column=Column(TIMESTAMP, server_default=func.now())
    )
    languaje: Optional[str] = Field(default="en")
    timezone: str
    notification: Optional[bool] = Field(default=True)
    notification_time: Optional[int] = Field(default=5)
    default_extension_period: Optional[int] = Field(default=60)
    default_waiting_list: Optional[bool] = Field(default=True)
    default_public: Optional[bool] = Field(default=True)
    default_allow_invitation: Optional[bool] = Field(default=True)
    default_show_members: Optional[bool] = Field(default=True)
    default_show_timezone: Optional[bool] = Field(default=True)

    user: "User" = Relationship(back_populates="settings")
