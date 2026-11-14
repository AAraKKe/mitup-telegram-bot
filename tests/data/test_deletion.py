import pytest
from structlog.testing import capture_logs

from mitup_bot.deletion import purge_meetups
from tests.helpers import MockApi, MockDbSession, create_joined_link, create_meetup, create_settings, create_user


@pytest.fixture
def api() -> MockApi:
    return MockApi()


async def test_purge_meetups_deletes_the_meetings_and_the_users_invited_into_them(
    mock_session: MockDbSession, api: MockApi
):
    first = create_meetup(id=1, title="First")
    second = create_meetup(id=2, title="Second")
    create_user(id=1, tg_user_id=10, owned_meetings=[first, second], settings=create_settings(id=1))
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=second, id=1)

    invitee_ids = await purge_meetups(mock_session, api, [first, second])

    assert invitee_ids == [3]
    assert "DELETE FROM meetups WHERE meetups.id IN (1, 2)" in mock_session.queries_executed
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed


async def test_purge_meetups_keeps_the_telegram_users_who_joined(mock_session: MockDbSession, api: MockApi):
    """Only the placeholder rows an invitation created are the meeting's to destroy; a real account
    outlives every meeting it joined."""
    meeting = create_meetup(id=1, title="Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    participant = create_user(id=2, tg_user_id=20, settings=create_settings(id=2))
    create_joined_link(user=participant, meetup=meeting, id=1)

    invitee_ids = await purge_meetups(mock_session, api, [meeting])

    assert invitee_ids == []
    # SQLAlchemy renders an empty IN as IN (NULL) AND (1 != 1).
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed


async def test_purge_meetups_names_the_invitees_it_destroys(mock_session: MockDbSession, api: MockApi):
    """The invitee rows have no other trace, so the purge has to name them before they go."""
    meeting = create_meetup(id=1, title="Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=meeting, id=1)

    with capture_logs() as logs:
        await purge_meetups(mock_session, api, [meeting])

    purged = next(entry for entry in logs if entry["event"] == "Invitee users purged")
    assert (purged["count"], purged["user_ids"]) == (1, [3])
    assert purged["reason"] == "cascade_of_purged_meetups"


async def test_purge_meetups_without_meetings_deletes_nothing(mock_session: MockDbSession, api: MockApi):
    assert await purge_meetups(mock_session, api, []) == []
    assert "DELETE FROM meetups WHERE meetups.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed


async def test_purge_meetups_rewrites_every_card_as_deleted_before_the_delete(
    mock_session: MockDbSession, api: MockApi
):
    """The cards render from the rows the DELETE removes, so each meeting is queued for the deleted
    banner while it still exists."""
    first = create_meetup(id=1, title="First")
    second = create_meetup(id=2, title="Second")
    create_user(id=1, tg_user_id=10, owned_meetings=[first, second], settings=create_settings(id=1))
    delete_already_run: list[bool] = []
    api.register_on_method(
        "update_meeting_messages",
        side_effect=lambda **_: delete_already_run.append(
            any("DELETE FROM meetups" in query for query in mock_session.queries_executed)
        ),
    )

    await purge_meetups(mock_session, api, [first, second])

    assert [call.kwargs for call in api.call_args_list("update_meeting_messages")] == [
        {"meeting": first, "was_deleted": True},
        {"meeting": second, "was_deleted": True},
    ]
    assert delete_already_run == [False, False]


async def test_purge_meetups_deletes_a_meeting_whose_cards_fail_to_render(mock_session: MockDbSession, api: MockApi):
    """The purge may run after notices already went out, so one card that cannot be rendered must
    not roll it back; that meeting's cards stay as they were and the failure is recorded."""
    broken = create_meetup(id=1, title="Broken")
    healthy = create_meetup(id=2, title="Healthy")
    create_user(id=1, tg_user_id=10, owned_meetings=[broken, healthy], settings=create_settings(id=1))
    api.register_on_method("update_meeting_messages", side_effect=[ValueError("card cannot render"), None])

    with capture_logs() as logs:
        await purge_meetups(mock_session, api, [broken, healthy])

    assert api.call_args("update_meeting_messages").kwargs == {"meeting": healthy, "was_deleted": True}
    assert "DELETE FROM meetups WHERE meetups.id IN (1, 2)" in mock_session.queries_executed
    failure = next(entry for entry in logs if entry["event"] == "Meeting cards not marked deleted")
    assert failure["log_level"] == "error"
    assert failure["meeting_id"] == 1
    assert failure["error_type"] == "builtins.ValueError"
    assert failure["reason"] == "render_failed"
