import datetime as dt
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlmodel.sql.expression import SelectOfScalar
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram.error import BadRequest

from mitup_bot import lifecycle
from mitup_bot.events import meetups_cleanup
from mitup_bot.events.service import EventType
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import MitupView
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

DELETED_MEETUP_IDS_PATTERN = re.compile(r"DELETE FROM meetups WHERE meetups\.id IN \(([^)]*)\)")

# The owners built here carry no supporter level, so the residue ages are measured against the free
# policy's windows — derived rather than pinned, so a retuned duration keeps the intended overdue days.
WARNING_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().deletion_warning_delay)
RETENTION_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().inactive_retention)


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.MEETUPS_CLEANUP.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def deleted_meetup_ids(session: MockDbSession) -> set[int]:
    """The meetup ids the run's DELETE removed, read back from the SQL it executed."""
    ids: set[int] = set()
    for query in session.queries_executed:
        match = DELETED_MEETUP_IDS_PATTERN.search(query)
        if match:
            ids.update(int(part) for part in match.group(1).split(",") if part.strip().isdigit())
    return ids


def purged_metric(level: SupporterLevel = SupporterLevel.NONE) -> dict[str, str]:
    """The dimensions one tier's `MeetupsDeleted` sample carries."""
    return {"EventType": EventType.MEETUPS_CLEANUP.value, "SupporterLevel": level.value}


def residue_record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    """The residue warning line for *event*; WARNING capture also picks up unrelated framework
    lines, so the lookup filters by the structlog event string (the LogRecord message)."""
    return next(record for record in caplog.records if record.message == event)


async def test_notify_no_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, ())
    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_ABOUT_TO_BE_DELETED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_notify_meeting_about_to_be_deleted(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Expiring Meeting", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (meeting,))

    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)
    await metrics_client.flush()

    expected_view = MitupView(
        description=NotificationMessages.DELETION_WARNING.get(
            lang=meeting.lang,
            meeting_title=meeting.title,
            days_until_deletion=7,
            past_meetings_button=ButtonMessages.PAST_MEETINGS.get(lang=meeting.user_language),
            reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.get(lang=meeting.user_language),
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get_text(lang=meeting.user_language),
                    callback_data=cb.REACTIVATE_MEETING.with_id(cast(int, meeting.id)),
                ),
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=meeting.user_language),
                    callback_data=cb.MAIN_MENU,
                ),
            ]
        ],
    )

    # MockApi does not override send_messages_to_users, so the real TelegramApi implementation
    # runs. It iterates over users and calls self.send_message_to_user(...) per user, which
    # MockApi does override and routes through call_mock → AsyncMock. The assertion therefore
    # lands on the real mock and is meaningful.
    api.assert_send_message_to_user_called(user=owner, view=expected_view)

    # The on_success callback fires after a successful send and sets this flag.
    assert meeting.expiration_notification_sent is True

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_ABOUT_TO_BE_DELETED,
        value=1,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_notify_unreachable_owner_marks_the_warning_as_sent(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    # Naive UTC, the shape the expiration column reads back with.
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=WARNING_DAYS + 2)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)
    await metrics_client.flush()

    assert owner.status is UserStatus.LEFT
    # The warning could never be delivered, so the meeting moves on to the deletion pool.
    assert meeting.expiration_notification_sent is True

    record = residue_record(caplog, "Expiration warning undelivered")
    assert record.__dict__["reason"] == meetups_cleanup.ResidueReason.OWNER_UNREACHABLE.value
    assert record.__dict__["meeting_id"] == 1
    assert record.__dict__["days_overdue"] == 2

    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_notify_unreachable_owner_meeting_is_not_warned_again(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)
    sends_after_first_run = api.mock_method("send_message_to_user").call_count

    # The next day's run re-derives its nomination set from the same predicate, which selects on
    # expiration_notification_sent being false.
    next_run_session = MockDbSession()
    still_unwarned = tuple(candidate for candidate in (meeting,) if not candidate.expiration_notification_sent)
    next_run_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, still_unwarned)

    await meetups_cleanup.notify_meetups_about_to_be_deleted(next_run_session, api, metrics_client)

    assert still_unwarned == ()
    assert api.mock_method("send_message_to_user").call_count == sends_after_first_run


async def test_notify_failed_send_leaves_the_meeting_in_the_warning_pool(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting = create_meetup(id=1, title="Unlucky Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    meeting.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=WARNING_DAYS + 1)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Bad Request: chat is temporarily unavailable")

    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)
    await metrics_client.flush()

    # A transient failure keeps the meeting in the warning pool: it stays eligible for the next run.
    assert meeting.expiration_notification_sent is False

    record = residue_record(caplog, "Expiration warning failed")
    assert record.__dict__["reason"] == meetups_cleanup.ResidueReason.OWNER_NOTIFICATION_FAILED.value
    assert record.__dict__["meeting_id"] == 1
    assert record.__dict__["days_overdue"] == 1

    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=1,
        properties={"failed_meeting_ids": [1]},
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_no_meetings(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, ())
    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=0,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED_UNNOTIFIED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_successfully(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="To Delete", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))

    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    await metrics_client.flush()

    expected_view = MitupView(
        description=NotificationMessages.DELETED.get(lang=meeting.lang, meeting_title=meeting.title),
        keyboard=[],
    )

    # MockApi does not override send_messages_to_users, so the real TelegramApi implementation
    # runs. It iterates over users and calls self.send_message_to_user(...) per user, which
    # MockApi does override and routes through call_mock → AsyncMock. The assertion therefore
    # lands on the real mock and is meaningful.
    api.assert_send_message_to_user_called(user=owner, view=expected_view)

    # The meeting was notified, so it is part of the DELETE.
    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # No outside users linked to this meeting; SQLAlchemy renders an empty IN as IN (NULL) AND (1 != 1).
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=1,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED_UNNOTIFIED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_with_outside_users(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="To Delete")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    # An invited (outside) user linked to this meeting
    outside_user = create_user(id=2, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=outside_user, meetup=meeting, id=1)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))

    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    await metrics_client.flush()

    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # The outside user (id=2) must be deleted; the owner (id=1, tg_user_id != -1) must not appear.
    assert "DELETE FROM users WHERE users.id IN (2)" in mock_session.queries_executed

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=1,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_whose_owner_is_unreachable(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_unnotified = create_meetup(id=2, title="Blocked Owner Meeting")
    owner_blocked = create_user(
        id=2, tg_user_id=20, owned_meetings=[meeting_unnotified], settings=create_settings(id=2)
    )
    # Naive UTC, the shape the expiration column reads back with.
    meeting_unnotified.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(
        days=RETENTION_DAYS + 2
    )

    # An invited user of the unnotified meeting must be purged with it, exactly as for a notified one.
    outside_user = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=outside_user, meetup=meeting_unnotified, id=1)

    mock_session.add_objects_with_statement(
        meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting_ok, meeting_unnotified)
    )
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    await metrics_client.flush()

    assert owner_blocked.status is UserStatus.LEFT
    # The owner blocked the bot, so the notice can never land — the meeting is deleted regardless.
    assert deleted_meetup_ids(mock_session) == {1, 2}
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed

    record = residue_record(caplog, "Meeting deleted without notifying its owner")
    assert record.__dict__["reason"] == meetups_cleanup.ResidueReason.OWNER_UNREACHABLE.value
    assert record.__dict__["meeting_id"] == 2
    assert record.__dict__["days_overdue"] == 2

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=2,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED_UNNOTIFIED,
        value=1,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    # An owner who cannot be reached is not a failure to delete.
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_whose_owner_is_unreachable_is_not_nominated_again(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    meeting = create_meetup(id=1, title="Blocked Owner Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = InactiveUserInteraction(10, private=True)

    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    sends_after_first_run = api.mock_method("send_message_to_user").call_count

    # The next day's run only sees the rows the first run left behind.
    surviving: tuple[Meetup, ...] = tuple(
        candidate for candidate in (meeting,) if candidate.id not in deleted_meetup_ids(mock_session)
    )
    next_run_session = MockDbSession()
    next_run_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, surviving)

    await meetups_cleanup.delete_meetups(next_run_session, api, metrics_client)

    assert surviving == ()
    assert api.mock_method("send_message_to_user").call_count == sends_after_first_run


async def test_delete_is_deferred_when_the_notice_raises(
    mock_session: MockDbSession,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    api: MockApi,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_failed = create_meetup(id=2, title="Unlucky Meeting")
    owner_failed = create_user(id=2, tg_user_id=20, owned_meetings=[meeting_failed], settings=create_settings(id=2))
    meeting_failed.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS + 3)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting_ok, meeting_failed))
    api.mock_method("send_message_to_user").side_effect = [None, BadRequest("Bad Request: chat is unavailable")]

    await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)
    await metrics_client.flush()

    # A transient Telegram failure is not evidence the owner is gone: the meeting stays.
    assert owner_failed.status is UserStatus.MEMBER
    assert deleted_meetup_ids(mock_session) == {1}

    record = residue_record(caplog, "Meeting deletion deferred")
    assert record.__dict__["reason"] == meetups_cleanup.ResidueReason.OWNER_NOTIFICATION_FAILED.value
    assert record.__dict__["meeting_id"] == 2
    assert record.__dict__["days_overdue"] == 3

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=1,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED_UNNOTIFIED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=1,
        properties={"failed_meeting_ids": [2]},
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


@pytest.mark.parametrize(
    ("statement", "duration_of"),
    [
        (meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, lambda policy: policy.deletion_warning_delay),
        (meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, lambda policy: policy.inactive_retention),
    ],
    ids=["deletion_warning", "permanent_deletion"],
)
def test_cleanup_statements_carry_a_branch_per_tier_window(
    statement: SelectOfScalar[Meetup], duration_of: Callable[[LifecyclePolicy], dt.timedelta]
):
    """Both cleanup windows are rendered from the policy per owner tier, measured from
    `expiration_time`. Which meetings each one selects is covered in
    tests/data/db_behavior/test_lifecycle_windows.py."""
    compiled = " ".join(
        str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).split()
    )

    for duration, levels in LifecyclePolicy.levels_by_duration(duration_of).items():
        rendered_levels = ", ".join(f"'{level.value}'" for level in levels)
        assert (
            f"users.supporter_level IN ({rendered_levels}) AND meetups.expiration_time "
            f"+ CAST('{LifecyclePolicy.interval_days(duration)} days' AS INTERVAL) < now()" in compiled
        )


async def test_run_orchestrates_both_functions(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, ())
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, ())

    await meetups_cleanup.run(api, metrics_client)
    await metrics_client.flush()

    metrics.assert_emitted(
        name=MetricKey.MEETUPS_ABOUT_TO_BE_DELETED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.EXPIRATION_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED,
        value=0,
        dimensions=purged_metric(),
    )
    metrics.assert_emitted(
        name=MetricKey.MEETUPS_DELETED_UNNOTIFIED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DELETION_FAILED,
        value=0,
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


# ---------------------------------------------------------------------------
# Decision records
# ---------------------------------------------------------------------------


async def test_warning_record_tells_a_delivered_notice_from_an_undeliverable_one(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """`expiration_notification_sent = True` is the precondition for the later permanent deletion
    and is written for both dispositions, so this line is the only place they stay apart."""
    reached = create_meetup(id=1, title="Reached Owner")
    create_user(id=1, tg_user_id=10, owned_meetings=[reached], settings=create_settings(id=1))
    blocked = create_meetup(id=2, title="Blocked Owner")
    create_user(id=2, tg_user_id=20, owned_meetings=[blocked], settings=create_settings(id=2))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (reached, blocked))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    with capture_logs(processors=[merge_contextvars]) as logs:
        await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics_client)

    records = {entry["meeting_id"]: entry for entry in logs if entry["event"] == "Meetup deletion warning recorded"}
    assert records[1]["delivery"] == meetups_cleanup.WarningDelivery.DELIVERED.value
    assert records[2]["delivery"] == meetups_cleanup.WarningDelivery.UNREACHABLE.value
    assert records[1]["tg_user_id"] == 10
    assert records[1]["window_days"] == WARNING_DAYS
    assert reached.expiration_notification_sent is True
    assert blocked.expiration_notification_sent is True


def test_warning_deadline_comes_from_the_owners_own_policy(monkeypatch: pytest.MonkeyPatch):
    """The message promises a deadline, so a Patron owner must be told their own tier's lead.

    Both tiers carry the same lead today, which is exactly why this test moves the Patron one: with
    the shipped values the assertion would hold whichever policy the view read.
    """
    patron_lead = dt.timedelta(days=14)
    monkeypatch.setattr(lifecycle, "PATRON_POLICY", replace(lifecycle.PATRON_POLICY, deletion_warning_lead=patron_lead))

    meeting = create_meetup(id=1, title="Patron Meeting")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1),
        supporter_level=SupporterLevel.HOST_3,
    )

    view = meetups_cleanup.deletion_warning_view(meeting)

    assert view.description == NotificationMessages.DELETION_WARNING.get(
        lang=meeting.lang,
        meeting_title=meeting.title,
        days_until_deletion=patron_lead.days,
        past_meetings_button=ButtonMessages.PAST_MEETINGS.get(lang=meeting.user_language),
        reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.get(lang=meeting.user_language),
    )


async def test_a_repeatedly_failing_send_escalates_instead_of_warning_daily_forever(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A send that raised defers the meeting to the next run, which re-nominates and re-warns it at
    the same level forever. Past the threshold the retry is not working, so the row gets an error of
    its own — that is what makes the failure counter mean "failing now" rather than "ever failed"."""
    meeting = create_meetup(id=1, title="Stuck Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(
        days=RETENTION_DAYS + meetups_cleanup.STUCK_RESIDUE_DAYS + 2
    )

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))
    api.mock_method("send_message_to_user").side_effect = BadRequest("Telegram is having a moment")

    with capture_logs(processors=[merge_contextvars]) as logs:
        await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)

    stuck = next(entry for entry in logs if entry["event"] == "Meeting deletion stuck")
    assert stuck["log_level"] == "error"
    assert stuck["meeting_id"] == 1
    assert stuck["days_overdue"] == meetups_cleanup.STUCK_RESIDUE_DAYS + 2
    assert stuck["reason"] == "owner_notification_failed_repeatedly"
    assert not [entry for entry in logs if entry["event"] == "Meeting deletion deferred"]


async def test_purge_names_the_meetings_and_the_invitees_it_destroys(
    mock_session: MockDbSession, metrics_client: MetricsClient, api: MockApi
):
    """A purge has to leave behind something the warnings that preceded it can be reconciled
    against — the meeting ids, the owner tiers, and the invitee rows that cascade with them."""
    meeting = create_meetup(id=1, title="Purged Meeting")
    create_user(
        id=1,
        tg_user_id=10,
        owned_meetings=[meeting],
        settings=create_settings(id=1),
        supporter_level=SupporterLevel.HOST_2,
    )
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=meeting, id=1)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))

    with capture_logs(processors=[merge_contextvars]) as logs:
        await meetups_cleanup.delete_meetups(mock_session, api, metrics_client)

    purged = next(entry for entry in logs if entry["event"] == "Meetups purged")
    assert (purged["count"], purged["meeting_ids"], purged["unnotified"]) == (1, [1], 0)
    assert purged["supporter_levels"] == {SupporterLevel.HOST_2.value: 1}
    assert purged["reason"] == "retention_elapsed"

    invitees = next(entry for entry in logs if entry["event"] == "Invitee users purged")
    assert (invitees["count"], invitees["user_ids"]) == (1, [3])

    summary = next(entry for entry in logs if entry["event"] == "Meetup purge sweep complete")
    assert (summary["purged"], summary["invitee_users_purged"]) == (1, 1)
