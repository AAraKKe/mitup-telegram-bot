from contextlib import contextmanager
from typing import Generator

from pydantic import PrivateAttr
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine
from pydantic import PrivateAttr
from sqlalchemy.engine import Engine

from mitup_bot.config import DbConfig
from .exceptions import MissingSessionError

from .exceptions import MissingSessionError


class MitupBaseModel(SQLModel):
    _engine: Engine | None = PrivateAttr(default=None)
    _session: Session | None = PrivateAttr(default=None)

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @classmethod
    def set_engine(cls, config: DbConfig):
        cls.postgres_url = config.full_url
        cls._engine = create_engine(config.full_url, echo=config.engine_echo)

    def update(self):
        """
        Update the instance in the database.
        """
        self.create()

    def create(self):
        """
        Create an entry in the database to represent the object
        """
        if self.__class__._session is None:
            raise MissingSessionError()

        self.__class__._session.add(self)
        self.__class__._session.commit()

    @classmethod
    @contextmanager
    def open_session(cls) -> Generator[Session, None, None]:
        with Session(bind=cls._engine) as session:
            cls._session = session
            yield session
            cls._session = None
