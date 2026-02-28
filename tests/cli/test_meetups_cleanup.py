from typing import cast

import pytest
from aws_embedded_metrics.unit import Unit

from mitup_bot.cli import meetups_cleanup
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.cli.meetups_cleanup import MEETUPS_DELETION_FAILED
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import ButtonConfig, MitupView
from tests.helpers import (
    MockApi,
    MockDbSession,
    StubMetrics,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)


@pytest.fixture
def metrics() -> StubMetrics:
    metrics = StubMetrics([])
    metrics.set_dimensions({"EventType": EventType.MEETUPS_CLEANUP.value})
    return metrics


async def test_notify_no_meetings(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, ())
    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics)
    await metrics.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_ABOUT_TO_BE_DELETED],
        [0],
        [Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_notify_meeting_about_to_be_deleted(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Expiring Meeting", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, (meeting,))

    await meetups_cleanup.notify_meetups_about_to_be_deleted(mock_session, api, metrics)
    await metrics.flush()

    expected_view = MitupView(
        description=NotificationMessages.MEETING_WILL_BE_PERMANENTLY_DELETED.get(
            lang=meeting.lang,
            meeting_title=meeting.title,
            days_until_deletion=7,
            past_meetings_button=ButtonMessages.PAST_MEETINGS.get(lang=meeting.user_language),
            reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.get(lang=meeting.user_language),
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get(lang=meeting.user_language),
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

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_ABOUT_TO_BE_DELETED],
        [1],
        [Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_no_meetings(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, ())
    await meetups_cleanup.delete_meetups(mock_session, api, metrics)
    await metrics.flush()

    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_DELETED, MetricKey.FAULT.with_prefix(MEETUPS_DELETION_FAILED)],
        [0, 0],
        [Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_successfully(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str):
    meeting = create_meetup(id=1, title="To Delete", language=lang)
    owner = create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1, language=lang))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))

    await meetups_cleanup.delete_meetups(mock_session, api, metrics)
    await metrics.flush()

    expected_view = MitupView(
        description=NotificationMessages.MEETING_PERMANENTLY_DELETED.get(
            lang=meeting.lang, meeting_title=meeting.title
        ),
        keyboard=[],
    )

    # MockApi does not override send_messages_to_users, so the real TelegramApi implementation
    # runs. It iterates over users and calls self.send_message_to_user(...) per user, which
    # MockApi does override and routes through call_mock → AsyncMock. The assertion therefore
    # lands on the real mock and is meaningful.
    api.assert_send_message_to_user_called(user=owner, view=expected_view)

    # The meeting's on_success callback appended its id to meeting_ids, so it appears in the DELETE.
    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # No outside users linked to this meeting; SQLAlchemy renders an empty IN as IN (NULL) AND (1 != 1).
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_DELETED, MetricKey.FAULT.with_prefix(MEETUPS_DELETION_FAILED)],
        [1, 0],
        [Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_meeting_with_outside_users(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    meeting = create_meetup(id=1, title="To Delete")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    # An invited (outside) user linked to this meeting
    outside_user = create_user(id=2, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=outside_user, meetup=meeting, id=1)

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting,))

    await meetups_cleanup.delete_meetups(mock_session, api, metrics)
    await metrics.flush()

    assert "DELETE FROM meetups WHERE meetups.id IN (1)" in mock_session.queries_executed
    # The outside user (id=2) must be deleted; the owner (id=1, tg_user_id != -1) must not appear.
    assert "DELETE FROM users WHERE users.id IN (2)" in mock_session.queries_executed

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_DELETED, MetricKey.FAULT.with_prefix(MEETUPS_DELETION_FAILED)],
        [1, 0],
        [Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_delete_partial_failure_inactive_user(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_fail = create_meetup(id=2, title="Fail Meeting")
    owner_fail = create_user(id=2, tg_user_id=20, owned_meetings=[meeting_fail], settings=create_settings(id=2))

    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, (meeting_ok, meeting_fail))

    # Simulate the second owner having blocked the bot. send_messages_to_users catches
    # InactiveUserInteraction, marks the user inactive, and skips on_success — so
    # meeting_fail is never added to meeting_ids and counts as a deletion failure.
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(20, private=True)]

    await meetups_cleanup.delete_meetups(mock_session, api, metrics)
    await metrics.flush()

    assert owner_fail.is_active is False

    metrics.assert_metrics_emited(
        [MetricKey.MEETUPS_DELETED, MetricKey.FAULT.with_prefix(MEETUPS_DELETION_FAILED)],
        [1, 1],
        [Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )


async def test_run_orchestrates_both_functions(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, ())
    mock_session.add_objects_with_statement(meetups_cleanup.MEETUPS_TO_DELETE_STATEMENT, ())

    await meetups_cleanup.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    metrics.assert_metrics_emited(
        [
            MetricKey.MEETUPS_ABOUT_TO_BE_DELETED,
            MetricKey.MEETUPS_DELETED,
            MetricKey.FAULT.with_prefix(MEETUPS_DELETION_FAILED),
        ],
        [0, 0, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.MEETUPS_CLEANUP.value},
    )
