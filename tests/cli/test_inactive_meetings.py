import datetime as dt
from unittest.mock import ANY

import pytest

from mitup_bot.cli import inactive_meetings
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.helpers import (
    MockApi,
    MockDbSession,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


async def test_no_meetings_to_deactivate(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    mock_session.add_objects_with_statement(inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, ())
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    api.assert_method_just_called("update_meeting_messages", times=0)
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_single_meeting_deactivated(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    mock_session.add_objects_with_statement(inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, (meeting,))
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is False
    assert meeting.expiration_time is not None
    assert isinstance(meeting.expiration_time, dt.datetime)

    # Verify update_meeting_messages was called with has_finished=True for this meeting
    call_kwargs = api.mock_method("update_meeting_messages").call_args.kwargs
    assert call_kwargs["has_finished"] is True
    assert call_kwargs["meeting"] is meeting
    assert call_kwargs["session"] is mock_session

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_meeting_with_invited_users(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting = create_meetup(id=1, title="Test Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))

    # Regular user (tg_user_id != -1) should NOT be deleted
    regular_user = create_user(id=2, tg_user_id=200)
    create_joined_link(user=regular_user, meetup=meeting, id=1)

    # Invited (outside) user (tg_user_id == -1) should be deleted
    invited_user = create_user(id=3, tg_user_id=-1, first_name="Outside User")
    create_joined_link(user=invited_user, meetup=meeting, id=2)

    mock_session.add_objects_with_statement(inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, (meeting,))
    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting.active is False

    # Only the invited user (id=3) should be targeted by the DELETE; the regular user (id=2) must not appear.
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed
    assert f"DELETE FROM messages WHERE messages.meetup_id = {meeting.id}" in mock_session.queries_executed

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )


async def test_api_failure_raises_runtime_error(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting_ok = create_meetup(id=1, title="OK Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_ok], settings=create_settings(id=1))

    meeting_fail = create_meetup(id=2, title="Fail Meeting")
    create_user(id=2, tg_user_id=20, owned_meetings=[meeting_fail], settings=create_settings(id=2))

    mock_session.add_objects_with_statement(
        inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, (meeting_ok, meeting_fail)
    )

    # First call succeeds (returns None), second call raises
    api.mock_method("update_meeting_messages").side_effect = [None, RuntimeError("API timeout")]

    with pytest.raises(RuntimeError, match="Failed to deactivate 1 meetings"):
        await inactive_meetings.run(api, metrics_client)

    await metrics_client.flush()

    # First meeting should still be deactivated
    assert meeting_ok.active is False
    assert meeting_ok.expiration_time is not None

    # Second meeting should remain active since its processing failed
    assert meeting_fail.active is True

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=1,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
        properties={"failed_details": ANY},
    )


async def test_multiple_meetings_deactivated(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions, api: MockApi
):
    meeting_a = create_meetup(id=1, title="Meeting A")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting_a], settings=create_settings(id=1))

    meeting_b = create_meetup(id=2, title="Meeting B")
    create_user(id=2, tg_user_id=20, owned_meetings=[meeting_b], settings=create_settings(id=2))

    mock_session.add_objects_with_statement(inactive_meetings.MEETINGS_TO_DEACTIVATE_STATEMENT, (meeting_a, meeting_b))

    await inactive_meetings.run(api, metrics_client)
    await metrics_client.flush()

    assert meeting_a.active is False
    assert meeting_b.active is False
    assert meeting_a.expiration_time is not None
    assert meeting_b.expiration_time is not None

    metrics.assert_emitted(
        name=MetricKey.MEETINGS_TO_DEACTIVATE,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATED,
        value=2,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
    metrics.assert_emitted(
        name=MetricKey.MEETINGS_DEACTIVATION_FAILED,
        value=0,
        dimensions={"EventType": EventType.DEACTIVATE_MEETINGS.value},
    )
