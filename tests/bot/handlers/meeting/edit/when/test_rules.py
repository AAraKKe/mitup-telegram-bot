import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from mitup_bot.handlers.meeting.edit.when import rules

MADRID = ZoneInfo("Europe/Madrid")
NEW_YORK = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("reference_datetime", "user_now", "expected_anchor"),
    [
        pytest.param(
            # 00:30 Madrid on 1 November is 23:30 UTC on 31 October.
            dt.datetime(2026, 10, 31, 23, 30, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 1, 9, 0, tzinfo=MADRID),
            dt.date(2026, 11, 1),
            id="madrid-just-after-midnight-opens-on-the-local-day",
        ),
        pytest.param(
            # 23:30 New York on 31 October is 03:30 UTC on 1 November.
            dt.datetime(2026, 11, 1, 3, 30, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 1, 9, 0, tzinfo=NEW_YORK),
            dt.date(2026, 10, 31),
            id="new-york-late-evening-opens-on-the-local-day",
        ),
        pytest.param(
            dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 6, 15, 9, 0, tzinfo=dt.UTC),
            dt.date(2026, 6, 20),
            id="future-reference-in-utc",
        ),
        pytest.param(
            dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 10, 2, 9, 0, tzinfo=MADRID),
            dt.date(2026, 10, 2),
            id="past-reference-falls-back-to-local-today",
        ),
        pytest.param(
            None,
            dt.datetime(2026, 10, 2, 0, 15, tzinfo=MADRID),
            dt.date(2026, 10, 2),
            id="no-reference-opens-on-local-today",
        ),
    ],
)
def test_safe_anchor_date(reference_datetime: dt.datetime | None, user_now: dt.datetime, expected_anchor: dt.date):
    assert rules.safe_anchor_date(reference_datetime, user_now) == expected_anchor
