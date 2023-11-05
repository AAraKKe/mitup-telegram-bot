from datetime import datetime as dt
from typing import Optional

from mitup_base_model import MitupBaseModel
from sqlalchemy import TIMESTAMP, BigInteger, Column
from sqlmodel import Field, SQLModel


class User(MitupBaseModel, SQLModel, table=True):
    __tablename__: str = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_time: Optional[dt] = Field(
        default=dt.utcnow(),
        sa_column=Column(TIMESTAMP),
    )
    updated_time: Optional[dt] = Field(
        default=dt.utcnow(),
        sa_column=Column(TIMESTAMP),
    )
    tg_user_id: int = Field(sa_column=Column(BigInteger))
    first_name: str
    last_name: Optional[str]
    username: Optional[str]
