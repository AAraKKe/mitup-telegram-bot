import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.db_test

# The 998_99x throwaway range keeps these clear of the lifecycle and backfill probes next door.
OWNER = 998_990
MEETING = 998_991

PROBE_OWNER_SQL = """
    INSERT INTO users (id, tg_user_id, first_name, status)
    VALUES (:owner, :owner, 'time-format-probe', 'member')
"""

PROBE_SETTINGS_SQL = """
    INSERT INTO settings (
        id, user_id, language, timezone, notification, notification_time, timeout,
        default_waiting_list, default_public, default_allow_invitation, default_incognito
    ) VALUES (:owner, :owner, 'en', 'UTC', true, 5, 5, false, false, false, false)
"""

PROBE_MEETUP_SQL = """
    INSERT INTO meetups (
        id, owner_id, title, waiting_list, public, allow_invitation, incognito,
        expiration_notification_sent, started_notification_sent, lock_on_start, active, activated_time
    ) VALUES
        (:meeting, :owner, 'time-format-probe', false, false, false, false,
         false, false, false, true, now())
"""


async def test_a_row_naming_no_time_format_reads_a_24_hour_clock_without_its_zone(db_session: AsyncSession):
    """Neither the rows already in the table nor the inserts the deployed image goes on running name
    these columns, so the server defaults are what both read."""
    savepoint = await db_session.begin_nested()
    try:
        for statement, params in [
            (PROBE_OWNER_SQL, {"owner": OWNER}),
            (PROBE_SETTINGS_SQL, {"owner": OWNER}),
            (PROBE_MEETUP_SQL, {"owner": OWNER, "meeting": MEETING}),
        ]:
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(statement).bindparams(**params)
            )

        meeting = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("SELECT show_timezone, clock_24h, date_format FROM meetups WHERE id = :meeting").bindparams(
                    meeting=MEETING
                )
            )
        ).one()
        settings = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "SELECT default_show_timezone, default_clock_24h, default_date_format"
                    " FROM settings WHERE id = :owner"
                ).bindparams(owner=OWNER)
            )
        ).one()

        assert tuple(meeting) == (False, True, "default")
        assert tuple(settings) == (False, True, "default")
    finally:
        await savepoint.rollback()
