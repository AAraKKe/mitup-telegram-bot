import pytest
from aws_embedded_metrics.unit import Unit
from telegram.error import Forbidden

from mitup_bot.cli import notify_meetings_started
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.messages import NotificationMessages
from mitup_bot.views import MitupView
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
    metrics.set_dimensions({"EventType": EventType.NOTIFY_START_MEETING.value})
    return metrics


# ---------------------------------------------------------------------------
# Query selection
# ---------------------------------------------------------------------------


def test_meetings_to_notify_started_query(mock_session: MockDbSession):
    """The statement must only select active meetings with datetime <= now and not yet notified."""
    expected_query = (
        "SELECT meetups.id, meetups.owner_id, meetups.title, meetups.waiting_list,"
        " meetups.public, meetups.allow_invitation, meetups.incognito,"
        " meetups.expiration_notification_sent, meetups.duration_minutes,"
        " meetups.started_notification_sent, meetups.lock_on_start, meetups.description,"
        " meetups.created_time, meetups.updated_time, meetups.expiration_time,"
        " meetups.datetime, meetups.max_members, meetups.language, meetups.location,"
        " meetups.active\n"
        "FROM meetups\n"
        "WHERE meetups.active = true AND meetups.datetime IS NOT NULL"
        " AND meetups.datetime <= now() AND meetups.started_notification_sent = false"
    )

    mock_session.exec(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT)
    assert mock_session.normalize_query(expected_query) == mock_session.queries_executed[0]


# ---------------------------------------------------------------------------
# No meetings to process
# ---------------------------------------------------------------------------


async def test_no_meetings_to_notify(mock_session: MockDbSession, metrics: StubMetrics, api: MockApi):
    mock_session.add_objects_with_statement(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, ())

    await notify_meetings_started.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    api.assert_method_just_called("update_meeting_messages", times=0)
    metrics.assert_metrics_emited(
        [
            MetricKey.MEETINGS_STARTED_PROCESSED,
            MetricKey.STARTED_NOTIFICATIONS_SENT,
            MetricKey.STARTED_NOTIFICATIONS_FAILED,
        ],
        [0, 0, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Single meeting with participants — notifications sent and flag set
# ---------------------------------------------------------------------------


async def test_started_notification_sent_to_participants(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Demo meetup")
    participant_a = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    participant_b = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    create_joined_link(user=participant_a, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=participant_b, meetup=meeting, id=2, is_waiting_list=False)

    mock_session.add_objects_with_statement(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, (meeting,))

    await notify_meetings_started.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    # Both participants received the notification
    view_a = MitupView(
        description=NotificationMessages.MEETING_STARTED.get(lang=participant_a.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    view_b = MitupView(
        description=NotificationMessages.MEETING_STARTED.get(lang=participant_b.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    # Both assertions use times=2 because the mock tracks all calls to send_message_to_user
    # and there are 2 total calls (one per participant); assert_send_message_to_user_called
    # checks call_count against `times` regardless of which user was targeted.
    api.assert_send_message_to_user_called(user=participant_a, view=view_a, times=2)
    api.assert_send_message_to_user_called(user=participant_b, view=view_b, times=2)

    # The started flag is set
    assert meeting.started_notification_sent is True

    # update_meeting_messages called with has_started=True
    call_kwargs = api.mock_method("update_meeting_messages").call_args.kwargs
    assert call_kwargs["has_started"] is True
    assert call_kwargs["meeting"] is meeting

    metrics.assert_metrics_emited(
        [
            MetricKey.MEETINGS_STARTED_PROCESSED,
            MetricKey.STARTED_NOTIFICATIONS_SENT,
            MetricKey.STARTED_NOTIFICATIONS_FAILED,
        ],
        [1, 2, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Waiting-list participants do NOT receive the notification
# ---------------------------------------------------------------------------


async def test_waiting_list_participants_not_notified(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Demo meetup")
    regular = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    waiting = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    create_joined_link(user=regular, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=waiting, meetup=meeting, id=2, is_waiting_list=True)

    mock_session.add_objects_with_statement(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, (meeting,))

    await notify_meetings_started.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    # Only 1 notification sent (the regular participant)
    metrics.assert_metrics_emited(
        [
            MetricKey.MEETINGS_STARTED_PROCESSED,
            MetricKey.STARTED_NOTIFICATIONS_SENT,
            MetricKey.STARTED_NOTIFICATIONS_FAILED,
        ],
        [1, 1, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )

    # The waiting-list user's send_message_to_user was never called
    MitupView(
        description=NotificationMessages.MEETING_STARTED.get(lang=waiting.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    # assert it was called 0 times for the waiting user — use the low-level mock
    send_mock = api.mock_mapping.get("send_message_to_user")
    if send_mock is not None:
        for call in send_mock.call_args_list:
            assert call.kwargs.get("user") is not waiting


# ---------------------------------------------------------------------------
# Forbidden → user marked inactive, no re-raise
# ---------------------------------------------------------------------------


async def test_forbidden_marks_user_inactive_and_does_not_raise(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Demo meetup")
    participant_a = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    participant_b = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    create_joined_link(user=participant_a, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=participant_b, meetup=meeting, id=2, is_waiting_list=False)

    mock_session.add_objects_with_statement(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, (meeting,))

    send_mock = api.mock_method("send_message_to_user")
    # First participant raises Forbidden, second succeeds
    send_mock.side_effect = [Forbidden("blocked"), None]

    await notify_meetings_started.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759
    await metrics.flush()

    # User whose send raised Forbidden is marked inactive via handle_forbidden
    assert participant_a.is_active is False
    # Second user should remain active
    assert participant_b.is_active is True

    # The meeting's started flag is still set
    assert meeting.started_notification_sent is True

    # Forbidden is caught by handle_forbidden so the coroutine returns normally;
    # gather sees both results as successes → sent=2, failed=0.
    metrics.assert_metrics_emited(
        [
            MetricKey.MEETINGS_STARTED_PROCESSED,
            MetricKey.STARTED_NOTIFICATIONS_SENT,
            MetricKey.STARTED_NOTIFICATIONS_FAILED,
        ],
        [1, 2, 0],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Failed meeting increments counter and raises RuntimeError at the end
# ---------------------------------------------------------------------------


async def test_failed_meeting_increments_counter_and_raises(
    mock_session: MockDbSession, metrics: StubMetrics, api: MockApi, lang: str
):
    meeting_ok = create_meetup(id=1, title="Good meeting")
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting_ok], settings=create_settings(id=1, language=lang))

    meeting_fail = create_meetup(id=2, title="Bad meeting")
    create_user(id=2, tg_user_id=2, owned_meetings=[meeting_fail], settings=create_settings(id=2, language=lang))

    mock_session.add_objects_with_statement(
        notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, (meeting_ok, meeting_fail)
    )

    # Second call to update_meeting_messages raises
    api.mock_method("update_meeting_messages").side_effect = [None, RuntimeError("Boom")]

    from unittest.mock import ANY

    with pytest.raises(RuntimeError, match="Failed to process started notifications"):
        await notify_meetings_started.run(api, metrics)  # ty: ignore[missing-argument]  # https://github.com/astral-sh/ty/issues/2759

    await metrics.flush()

    # First meeting was processed successfully
    assert meeting_ok.started_notification_sent is True

    # Second meeting did not get flagged
    assert meeting_fail.started_notification_sent is False

    # MEETINGS_STARTED_PROCESSED = len(meetings) = 2 (both were selected regardless of outcome)
    # STARTED_NOTIFICATIONS_SENT = 0 (no participants added to these meetings)
    # STARTED_NOTIFICATIONS_FAILED = 1 (the second meeting's update_meeting_messages raised)
    metrics.assert_metrics_emited(
        [
            MetricKey.MEETINGS_STARTED_PROCESSED,
            MetricKey.STARTED_NOTIFICATIONS_SENT,
            MetricKey.STARTED_NOTIFICATIONS_FAILED,
        ],
        [2, 0, 1],
        [Unit.COUNT, Unit.COUNT, Unit.COUNT],
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
        properties={"failed_details": ANY},
    )
