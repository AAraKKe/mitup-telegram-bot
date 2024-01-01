import datetime as dt
from typing import TYPE_CHECKING, Optional

from sqlalchemy import TIMESTAMP, Column
from sqlmodel import Field, SQLModel, Relationship

from .mitup_base_model import MitupBaseModel

if TYPE_CHECKING:
    from .users import User


class Settings(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "settings"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    created_time: Optional[dt.datetime] = Field(
        default=None, sa_column=Column(TIMESTAMP)
    )
    updated_time: Optional[dt.datetime] = Field(
        default=None, sa_column=Column(TIMESTAMP)
    )
    languaje: Optional[str] = Field(default=None)
    user: "User" = Relationship(back_populates="settings")
