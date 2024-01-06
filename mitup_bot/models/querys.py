from . import User, Settings, MitupBaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select


def get_user_by_tg_user_id(tg_user_id: int) -> User:
    with Session(engine) as session:
        statement = select(User).where(User.tg_user_id == tg_user_id)
        return session.exec(statement)
