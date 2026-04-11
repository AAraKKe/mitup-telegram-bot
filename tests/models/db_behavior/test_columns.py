import pytest
from sqlalchemy import text
from sqlmodel import Session

pytestmark = pytest.mark.db_test


def test_users_has_is_active(db_session: Session) -> None:
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/376
        text("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='is_active'")
    ).scalar_one()
    assert result == "is_active"


def test_joined_users_has_invited_by_id(db_session: Session) -> None:
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/376
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='joined_users' AND column_name='invited_by_id'"
        )
    ).scalar_one()
    assert result == "invited_by_id"


def test_messages_has_chat_instance(db_session: Session) -> None:
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/376
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='messages' AND column_name='chat_instance'"
        )
    ).scalar_one()
    assert result == "chat_instance"


@pytest.mark.parametrize("table_name, column_name", [("meetups", "location"), ("messages", "buttons")])
def test_column_is_json(db_session: Session, table_name: str, column_name: str) -> None:
    data_type = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/376
        text(
            "SELECT data_type FROM information_schema.columns WHERE table_name=:table AND column_name=:col"
        ).bindparams(table=table_name, col=column_name)
    ).scalar_one()
    assert data_type == "json"
