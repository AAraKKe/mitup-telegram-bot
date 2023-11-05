from datetime import datetime as dt

from sqlmodel import Session, SQLModel, create_engine


class MitupBaseModel(SQLModel):
    @classmethod
    def set_engine(cls, postgres_url: str):
        cls.postgres_url = postgres_url
        cls.engine = create_engine(postgres_url, echo=True)

    @classmethod
    def get_url(cls):
        return cls.postgres_url

    def update(self):
        if hasattr(self, "updated_time"):
            self.updated_time = dt.utcnow()

        with Session(bind=self.engine) as session:
            session.add(self)
            session.commit()
