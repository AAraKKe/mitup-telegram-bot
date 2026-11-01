import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot.db

pytestmark = pytest.mark.db_test

VERSIONS_DIR = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(mitup_bot.db.__file__).parent / "migrations" / "versions"
)


def load_predicate() -> str:
    """The exact WHERE clause the backfill runs, read out of the revision module.

    Revision modules can't be imported by dotted path: their names start with a digit.
    """
    filename = "a83d5e0c6f19_backfill_member_time_from_created_time.py"
    spec = importlib.util.spec_from_file_location("migration_member_time_backfill", VERSIONS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MEMBERS_WITHOUT_MEMBER_TIME_PREDICATE


MEMBERS_WITHOUT_MEMBER_TIME_PREDICATE = load_predicate()

# The 998_6xx throwaway range keeps these clear of the session-scoped seed users.
MEMBER = 998_601
LEFT = 998_602
DELETION_REQUESTED = 998_603
JOINED_ONLY = 998_604
ONBOARDING = 998_605
ALREADY_STAMPED = 998_606

PROBE_ROWS_SQL = """
    INSERT INTO users (tg_user_id, first_name, status, member_time) VALUES
        (:member, 'backfill-probe', 'member', NULL),
        (:left_user, 'backfill-probe', 'left', NULL),
        (:deletion_requested, 'backfill-probe', 'deletion_requested', NULL),
        (:joined_only, 'backfill-probe', 'joined_only', NULL),
        (:onboarding, 'backfill-probe', 'onboarding', NULL),
        (:stamped, 'backfill-probe', 'member', '2026-08-01T00:00:00+00:00')
"""


async def test_the_backfill_claims_rows_that_were_members_and_nothing_else(db_session: AsyncSession):
    """Evaluated as a SELECT: the UPDATE would lock every matching row inside the suite's transaction."""
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(PROBE_ROWS_SQL).bindparams(
                member=MEMBER,
                left_user=LEFT,
                deletion_requested=DELETION_REQUESTED,
                joined_only=JOINED_ONLY,
                onboarding=ONBOARDING,
                stamped=ALREADY_STAMPED,
            )
        )

        claimed = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "SELECT tg_user_id FROM users"
                    f" WHERE {MEMBERS_WITHOUT_MEMBER_TIME_PREDICATE} AND first_name = 'backfill-probe'"
                )
            )
        ).all()

        assert sorted(row.tg_user_id for row in claimed) == [MEMBER, LEFT, DELETION_REQUESTED]
    finally:
        await savepoint.rollback()
