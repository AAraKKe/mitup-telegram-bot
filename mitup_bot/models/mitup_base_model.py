import datetime as dt
from datetime import timezone
from contextlib import contextmanager
from typing import Any, Generator

from pydantic import PrivateAttr
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from mitup_bot.config import DbConfig

from .exceptions import MissingSessionError


class MitupBaseModel(SQLModel):
    # This is the right way to define engine and session but it's not working
    # https://github.com/tiangolo/sqlmodel/pull/472
    # For this reason, we're defining get and set methods where we are setting internaly
    # the variables

    # _engine: Engine | None = PrivateAttr(default=None)
    # _session: Session | None = PrivateAttr(default=None)

    # model_config = {
    #     "arbitrary_types_allowed": True,
    # }

    @classmethod
    def get_engine(cls) -> Engine | None:
        return cls._engine if hasattr(cls, "_engine") else None

    @classmethod
    def get_session(cls) -> Session | None:
        return cls._session if hasattr(cls, "_session") else None

    @classmethod
    def set_engine(cls, config: DbConfig):
        cls._engine = create_engine(config.full_url, echo=config.engine_echo)

    def update(self):
        """
        Update a given entry in the database. If the object has an `updated_time` field it will
        be automatically updated to the current UTC time.
        """
        session = self.__class__.get_session()
        if session is None:
            raise MissingSessionError()

        if hasattr(self, "updated_time"):
            self.updated_time = dt.datetime.now(timezone.utc)

            session.add(self)
            session.commit()

    def create(self):
        """
        Create an entry in the database to represent the object
        """
        session = self.__class__.get_session()
        if session is None:
            raise MissingSessionError()

        session.add(self)
        session.commit()

    @classmethod
    @contextmanager
    def open_session(cls) -> Generator[Session, None, None]:
        with Session(bind=cls.get_engine()) as session:
            cls._session = session
            yield session
            cls._session = None
