import pytest

from mitup_bot.events import notify_meetings_started
from mitup_bot.events.service import EventType
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.utils.messages import NotificationMessages
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

# Inactive-user handling is not exercised here: under the write lifecycle an unreachable
# participant surfaces at drain time and is marked inactive by the reconcile transaction —
# see the reconcile tests in tests/test_db.py and the real-Postgres lifecycle tests in
# tests/models/db_behavior/test_events_write_lifecycle.py.


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.NOTIFY_START_MEETING.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def register_due_meetings(mock_session: MockDbSession, *meetings: Meetup, still_due: bool = True):
    """Register a meeting for both reads the job performs: the unlocked candidate sweep and
    the per-meeting re-check inside the write lifecycle (empty when ``still_due`` is False —
    the meeting was flagged or deactivated between sweep and processing)."""
    mock_session.add_objects_with_statement(notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT, meetings)
    for meeting in meetings:
        mock_session.add_objects_with_statement(
            notify_meetings_started.MEETINGS_TO_NOTIFY_STARTED_STATEMENT.where(Meetup.id == meeting.id),
            (meeting,) if still_due else (),
        )


# ---------------------------------------------------------------------------
# Query selection
# ---------------------------------------------------------------------------


def test_meetings_to_notify_started_query(mock_session: MockDbSession):
    """The statement must only select active meetings with datetime <= now and not yet notified."""
    expected_query = (
        "SELECT meetups.id, meetups.owner_id, meetups.title, meetups.waiting_list,"
        " meetups.public, meetups.allow_invitation, meetups.incognito,"
        " meetups.expiration_notification_sent, meetups.end_datetime,"
        " meetups.started_notification_sent, meetups.lock_on_start, meetups.description,"
        " meetups.created_time, meetups.updated_time, meetups.activated_time, meetups.expiration_time,"
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


async def test_no_meetings_to_notify(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    register_due_meetings(mock_session)

    await notify_meetings_started.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("update_meeting_messages", times=0)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_STARTED_PROCESSED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Single meeting with participants — notifications sent and flag set
# ---------------------------------------------------------------------------


async def test_started_notification_sent_to_participants(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Demo meetup")
    participant_a = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    participant_b = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    create_joined_link(user=participant_a, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=participant_b, meetup=meeting, id=2, is_waiting_list=False)

    register_due_meetings(mock_session, meeting)

    await notify_meetings_started.run(api, metrics_client)
    await metrics_client.flush()

    # Both participants received the notification
    view_a = MitupView(
        description=NotificationMessages.STARTED.get(lang=participant_a.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    view_b = MitupView(
        description=NotificationMessages.STARTED.get(lang=participant_b.lang, meeting_title=meeting.title),
        keyboard=[],
    )
    # Both assertions use times=2 because the mock tracks all calls to send_message_to_user
    # and there are 2 total calls (one per participant); assert_send_message_to_user_called
    # checks call_count against `times` regardless of which user was targeted.
    api.assert_send_message_to_user_called(user=participant_a, view=view_a, times=2)
    api.assert_send_message_to_user_called(user=participant_b, view=view_b, times=2)

    # The started flag is set
    assert meeting.started_notification_sent is True

    # update_meeting_messages was called for this meeting
    call_kwargs = api.mock_method("update_meeting_messages").call_args.kwargs
    assert call_kwargs["meeting"] is meeting

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_STARTED_PROCESSED,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=2,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Meeting no longer due at the per-meeting re-check — skipped, not failed
# ---------------------------------------------------------------------------


async def test_meeting_no_longer_due_is_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    """The per-meeting re-check found the meeting already flagged or deactivated: nothing is
    sent, nothing fails — the next sweep re-evaluates from scratch."""
    meeting = create_meetup(id=1, title="Already flagged meetup")
    participant = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    create_joined_link(user=participant, meetup=meeting, id=1, is_waiting_list=False)

    register_due_meetings(mock_session, meeting, still_due=False)

    await notify_meetings_started.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.started_notification_sent is False
    api.assert_method_just_called("update_meeting_messages", times=0)
    api.assert_method_just_called("send_message_to_user", times=0)

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_STARTED_PROCESSED,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )


# ---------------------------------------------------------------------------
# Waiting-list participants do NOT receive the notification
# ---------------------------------------------------------------------------


async def test_waiting_list_participants_not_notified(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting = create_meetup(id=1, title="Demo meetup")
    regular = create_user(id=1, tg_user_id=1, settings=create_settings(id=1, language=lang))
    waiting = create_user(id=2, tg_user_id=2, settings=create_settings(id=2, language=lang))
    create_joined_link(user=regular, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=waiting, meetup=meeting, id=2, is_waiting_list=True)

    register_due_meetings(mock_session, meeting)

    await notify_meetings_started.run(api, metrics_client)
    await metrics_client.flush()

    # Only 1 notification sent (the regular participant)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_STARTED_PROCESSED,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )

    # The waiting-list user's send_message_to_user was never called
    send_mock = api.mock_mapping.get("send_message_to_user")
    assert send_mock is not None
    for call in send_mock.call_args_list:
        assert call.kwargs.get("user") is not waiting


# ---------------------------------------------------------------------------
# JOINED_ONLY participants are skipped at iteration time
# ---------------------------------------------------------------------------


async def test_joined_only_participants_not_notified(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    """JOINED_ONLY users cannot be DM-ed; they must be filtered before send_message_to_user.

    Were they included, every send would fail during the drain — the iteration-time filter
    avoids ever enqueueing them.
    """
    meeting = create_meetup(id=1, title="Demo meetup")
    member_participant = create_user(
        id=1, tg_user_id=1, status=UserStatus.MEMBER, settings=create_settings(id=1, language=lang)
    )
    joined_only_participant = create_user(
        id=2, tg_user_id=2, status=UserStatus.JOINED_ONLY, settings=create_settings(id=2, language=lang)
    )
    create_joined_link(user=member_participant, meetup=meeting, id=1, is_waiting_list=False)
    create_joined_link(user=joined_only_participant, meetup=meeting, id=2, is_waiting_list=False)

    register_due_meetings(mock_session, meeting)

    await notify_meetings_started.run(api, metrics_client)
    await metrics_client.flush()

    # Only the MEMBER participant gets a notification — the JOINED_ONLY user is filtered out.
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )

    # The JOINED_ONLY user was never targeted.
    send_mock = api.mock_mapping.get("send_message_to_user")
    assert send_mock is not None
    for call in send_mock.call_args_list:
        assert call.kwargs.get("user") is not joined_only_participant

    # The JOINED_ONLY user's status is unchanged.
    assert joined_only_participant.status is UserStatus.JOINED_ONLY


# ---------------------------------------------------------------------------
# Failed meeting increments counter, does not block others, raises at the end
# ---------------------------------------------------------------------------


async def test_failed_meeting_increments_counter_and_raises(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi, lang: str
):
    meeting_ok = create_meetup(id=1, title="Good meeting")
    create_user(id=1, tg_user_id=1, owned_meetings=[meeting_ok], settings=create_settings(id=1, language=lang))

    meeting_fail = create_meetup(id=2, title="Bad meeting")
    create_user(id=2, tg_user_id=2, owned_meetings=[meeting_fail], settings=create_settings(id=2, language=lang))

    register_due_meetings(mock_session, meeting_ok, meeting_fail)

    # Second call to update_meeting_messages raises
    api.mock_method("update_meeting_messages").side_effect = [None, RuntimeError("Boom")]

    with pytest.raises(RuntimeError, match="Failed to process started notifications"):
        await notify_meetings_started.run(api, metrics_client)

    await metrics_client.flush()

    # First meeting committed in its own write lifecycle, so the second meeting's failure
    # cannot roll it back
    assert meeting_ok.started_notification_sent is True

    # Second meeting did not get flagged
    assert meeting_fail.started_notification_sent is False

    # MEETINGS_STARTED_PROCESSED = len(meetings) = 2 (both were selected regardless of outcome)
    # STARTED_NOTIFICATIONS_SENT = 0 (no participants added to these meetings)
    # STARTED_NOTIFICATIONS_FAILED = 1 (the second meeting's update_meeting_messages raised)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_STARTED_PROCESSED,
        value=2,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_SENT,
        value=0,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
    )
    # The failure counter is a number, not a report: the per-meeting id and error are on the
    # `Failed to process started notification for meeting` log line, so nothing rides the record.
    metrics.assert_emitted(
        name=MetricKey.STARTED_NOTIFICATIONS_FAILED,
        value=1,
        dimensions={"EventType": EventType.NOTIFY_START_MEETING.value},
        properties={},
        properties_exact=True,
    )
