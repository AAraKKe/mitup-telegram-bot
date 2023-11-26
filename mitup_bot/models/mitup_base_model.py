import datetime as dt
from datetime import timezone

from sqlmodel import Session, SQLModel, create_engine

from mitup_bot.config import DbConfig


class MitupBaseModel(SQLModel):
    @classmethod
    def set_engine(cls, config: DbConfig):
        cls.postgres_url = config.full_url
        cls.engine = create_engine(config.full_url, echo=config.engine_echo)

    def update(self):
        """
        Update a given entry in the database. If the object has an `updated_time` field it will
        be automatically updated to the current UTC time.
        """
        if hasattr(self, "updated_time"):
            self.updated_time = dt.datetime.now(timezone.utc)

        with Session(bind=self.engine) as session:
            session.add(self)
            session.commit()

    def create(self):
        """
        Create an entry in the database to represent the object
        """
        with Session(bind=self.engine) as session:
            session.add(self)
            session.commit()
