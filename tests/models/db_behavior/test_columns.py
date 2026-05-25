import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

pytestmark = pytest.mark.db_test


def test_users_has_status_column(db_session: Session):
    """The `status` column must exist on `users`, NOT NULL, with a 16-char VARCHAR backing."""
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(
            "SELECT column_name, is_nullable, data_type, character_maximum_length"
            " FROM information_schema.columns"
            " WHERE table_name='users' AND column_name='status'"
        )
    ).one()
    column_name, is_nullable, data_type, max_length = result
    assert column_name == "status"
    assert is_nullable == "NO"
    # SQLAlchemy String(16) renders as character varying with length 16 on Postgres.
    assert data_type == "character varying"
    assert max_length == 16


def test_users_status_check_constraint_exists(db_session: Session):
    """The `users_status_valid` CHECK constraint must be present and enforce the 3 enum values."""
    constraint = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(
            "SELECT pg_get_constraintdef(c.oid)"
            " FROM pg_constraint c"
            " JOIN pg_class t ON c.conrelid = t.oid"
            " WHERE t.relname = 'users' AND c.conname = 'users_status_valid'"
        )
    ).scalar_one()
    # Phase 1 migration uses the literal `status IN ('member','joined_only','left')`.
    assert "'member'" in constraint
    assert "'joined_only'" in constraint
    assert "'left'" in constraint


@pytest.mark.parametrize("status_value", ["member", "joined_only", "left"])
def test_users_status_accepts_valid_values(db_session: Session, status_value: str):
    """Each enum value defined in `UserStatus` must satisfy the CHECK constraint."""
    # Use the 998 throwaway range to avoid colliding with session-scoped seed users.
    tg_user_id = 998_100 + ["member", "joined_only", "left"].index(status_value)
    # Savepoint isolates the insert from the session-scoped outer transaction.
    savepoint = db_session.begin_nested()
    try:
        db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "INSERT INTO users (tg_user_id, first_name, status)"
                " VALUES (:tg_user_id, 'check-constraint-probe', :status_value)"
            ).bindparams(tg_user_id=tg_user_id, status_value=status_value)
        )
        db_session.flush()
    finally:
        savepoint.rollback()


def test_users_status_rejects_invalid_value(db_session: Session):
    """An unknown status string must trigger the CHECK constraint violation."""
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "INSERT INTO users (tg_user_id, first_name, status)"
                    " VALUES (998200, 'check-constraint-probe', 'banned')"
                )
            )
            db_session.flush()
    finally:
        savepoint.rollback()


def test_joined_users_has_invited_by_id(db_session: Session) -> None:
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='joined_users' AND column_name='invited_by_id'"
        )
    ).scalar_one()
    assert result == "invited_by_id"


def test_messages_has_chat_instance(db_session: Session) -> None:
    result = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='messages' AND column_name='chat_instance'"
        )
    ).scalar_one()
    assert result == "chat_instance"


@pytest.mark.parametrize("table_name, column_name", [("meetups", "location"), ("messages", "buttons")])
def test_column_is_json(db_session: Session, table_name: str, column_name: str) -> None:
    data_type = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(
            "SELECT data_type FROM information_schema.columns WHERE table_name=:table AND column_name=:col"
        ).bindparams(table=table_name, col=column_name)
    ).scalar_one()
    assert data_type == "json"
