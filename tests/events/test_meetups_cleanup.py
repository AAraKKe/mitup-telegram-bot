import datetime as dt
import logging
import re
from typing import cast

import pytest
from telegram.error import BadRequest

from mitup_bot.events import meetups_cleanup
from mitup_bot.events.service import EventType
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
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
    meeting.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=175)

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
    meeting.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=174)

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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
    meeting_unnotified.expiration_time = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=182)

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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
    meeting_failed.expiration_time = dt.datetime.now(dt.UTC) - dt.timedelta(days=183)

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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
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
