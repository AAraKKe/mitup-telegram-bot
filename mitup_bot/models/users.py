import datetime as dt
from typing import Optional

from sqlalchemy import TIMESTAMP, BigInteger, Column
from sqlmodel import Field, SQLModel

from .mitup_base_model import MitupBaseModel


class User(MitupBaseModel, SQLModel, table=True):
    # Until better configuration is available through SQLModel (https://github.com/tiangolo/sqlmodel/issues/159)
    __tablename__: str = "users"  # type: ignore

    first_name: str
    tg_user_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    id: Optional[int] = Field(default=None, primary_key=True)
    created_time: Optional[dt.datetime] = Field(
        default=None, sa_column=Column(TIMESTAMP)
    )
    updated_time: Optional[dt.datetime] = Field(
        default=None, sa_column=Column(TIMESTAMP)
    )
    last_name: Optional[str]
    username: Optional[str]
