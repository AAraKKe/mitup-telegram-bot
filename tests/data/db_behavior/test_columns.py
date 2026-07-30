import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.acquisition import ACQUISITION_SOURCE_MAX_LENGTH

pytestmark = pytest.mark.db_test


async def test_users_has_status_column(db_session: AsyncSession):
    """The `status` column must exist on `users`, NOT NULL, with a 32-char VARCHAR backing."""
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT column_name, is_nullable, data_type, character_maximum_length"
                " FROM information_schema.columns"
                " WHERE table_name='users' AND column_name='status'"
            )
        )
    ).one()
    column_name, is_nullable, data_type, max_length = result
    assert column_name == "status"
    assert is_nullable == "NO"
    # SQLAlchemy String(32) renders as character varying with length 32 on Postgres.
    assert data_type == "character varying"
    assert max_length == 32


async def test_users_has_acquisition_source_column(db_session: AsyncSession):
    """The `acquisition_source` column must exist on `users`, nullable, with a 64-char VARCHAR
    backing — the width Telegram's deep-link payload cap gives it."""
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT is_nullable, data_type, character_maximum_length"
                " FROM information_schema.columns"
                " WHERE table_name='users' AND column_name='acquisition_source'"
            )
        )
    ).one()
    is_nullable, data_type, max_length = result
    assert is_nullable == "YES"
    assert data_type == "character varying"
    assert max_length == ACQUISITION_SOURCE_MAX_LENGTH


async def test_users_status_check_constraint_exists(db_session: AsyncSession):
    """The `users_status_valid` CHECK constraint must be present and enforce the 4 enum values."""
    constraint = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT pg_get_constraintdef(c.oid)"
                " FROM pg_constraint c"
                " JOIN pg_class t ON c.conrelid = t.oid"
                " WHERE t.relname = 'users' AND c.conname = 'users_status_valid'"
            )
        )
    ).scalar_one()
    assert "'member'" in constraint
    assert "'joined_only'" in constraint
    assert "'left'" in constraint
    assert "'deletion_requested'" in constraint


@pytest.mark.parametrize("status_value", ["member", "joined_only", "left", "deletion_requested"])
async def test_users_status_accepts_valid_values(db_session: AsyncSession, status_value: str):
    """Each enum value defined in `UserStatus` must satisfy the CHECK constraint."""
    # Use the 998 throwaway range to avoid colliding with session-scoped seed users.
    tg_user_id = 998_100 + ["member", "joined_only", "left", "deletion_requested"].index(status_value)
    # Savepoint isolates the insert from the session-scoped outer transaction.
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "INSERT INTO users (tg_user_id, first_name, status)"
                " VALUES (:tg_user_id, 'check-constraint-probe', :status_value)"
            ).bindparams(tg_user_id=tg_user_id, status_value=status_value)
        )
        await db_session.flush()
    finally:
        await savepoint.rollback()


async def test_users_status_rejects_invalid_value(db_session: AsyncSession):
    """An unknown status string must trigger the CHECK constraint violation."""
    savepoint = await db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "INSERT INTO users (tg_user_id, first_name, status)"
                    " VALUES (998200, 'check-constraint-probe', 'banned')"
                )
            )
            await db_session.flush()
    finally:
        await savepoint.rollback()


async def test_joined_users_has_invited_by_id(db_session: AsyncSession):
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='joined_users' AND column_name='invited_by_id'"
            )
        )
    ).scalar_one()
    assert result == "invited_by_id"


async def test_messages_has_chat_instance(db_session: AsyncSession):
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name='messages' AND column_name='chat_instance'"
            )
        )
    ).scalar_one()
    assert result == "chat_instance"


@pytest.mark.parametrize("table_name, column_name", [("meetups", "location"), ("messages", "buttons")])
async def test_column_is_json(db_session: AsyncSession, table_name: str, column_name: str):
    data_type = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT data_type FROM information_schema.columns WHERE table_name=:table AND column_name=:col"
            ).bindparams(table=table_name, col=column_name)
        )
    ).scalar_one()
    assert data_type == "json"
