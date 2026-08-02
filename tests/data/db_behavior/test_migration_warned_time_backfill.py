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
    """The exact WHERE clause the warned_time backfill runs, read out of the revision module.

    Revision modules can't be imported by dotted path — their names start with a digit.
    """
    filename = "ed11fdab83a6_add_warned_time_to_meetups.py"
    spec = importlib.util.spec_from_file_location("migration_warned_time", VERSIONS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.WARNED_ROWS_PREDICATE


WARNED_ROWS_PREDICATE = load_predicate()

# The 998_98x throwaway range keeps these clear of the lifecycle-window scenarios next door.
OWNER = 998_980
WARNED_WITHOUT_A_STAMP = 998_981
WARNED_AND_STAMPED = 998_982
NEVER_WARNED = 998_983

PROBE_OWNER_SQL = """
    INSERT INTO users (id, tg_user_id, first_name, status)
    VALUES (:owner, :owner, 'backfill-probe', 'member')
"""

PROBE_ROWS_SQL = """
    INSERT INTO meetups (
        id, owner_id, title, waiting_list, public, allow_invitation, incognito,
        expiration_notification_sent, started_notification_sent, lock_on_start, active,
        activated_time, warned_time
    ) VALUES
        (:unstamped, :owner, 'backfill-probe', false, false, false, false,
         true, false, false, false, now(), NULL),
        (:stamped, :owner, 'backfill-probe', false, false, false, false,
         true, false, false, false, now(), now() - interval '3 days'),
        (:never, :owner, 'backfill-probe', false, false, false, false,
         false, false, false, false, now(), NULL)
"""


async def test_the_backfill_claims_warned_rows_that_carry_no_stamp(db_session: AsyncSession):
    """Every row already flagged as warned needs a stamp, because without one the deletion gate can
    never open for it again. A row that already has one is left alone — re-stamping it would hand its
    owner a second lead — and a row that was never warned has nothing to date.

    The predicate is evaluated instead of the UPDATE it belongs to: the statement matches on the flag
    alone, so running it inside the suite's long-lived transaction would lock every warned row.
    """
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(PROBE_OWNER_SQL).bindparams(owner=OWNER)
        )
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(PROBE_ROWS_SQL).bindparams(
                owner=OWNER, unstamped=WARNED_WITHOUT_A_STAMP, stamped=WARNED_AND_STAMPED, never=NEVER_WARNED
            )
        )

        claimed = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(f"SELECT id FROM meetups WHERE {WARNED_ROWS_PREDICATE} AND title = 'backfill-probe'")
            )
        ).all()

        assert [row.id for row in claimed] == [WARNED_WITHOUT_A_STAMP]
    finally:
        await savepoint.rollback()
