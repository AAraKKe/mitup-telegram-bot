from structlog.testing import capture_logs

from mitup_bot.deletion import purge_meetups
from tests.helpers import MockDbSession, create_joined_link, create_meetup, create_settings, create_user


async def test_purge_meetups_deletes_the_meetings_and_the_users_invited_into_them(mock_session: MockDbSession):
    first = create_meetup(id=1, title="First")
    second = create_meetup(id=2, title="Second")
    create_user(id=1, tg_user_id=10, owned_meetings=[first, second], settings=create_settings(id=1))
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=second, id=1)

    invitee_ids = await purge_meetups(mock_session, [first, second])

    assert invitee_ids == [3]
    assert "DELETE FROM meetups WHERE meetups.id IN (1, 2)" in mock_session.queries_executed
    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed


async def test_purge_meetups_keeps_the_telegram_users_who_joined(mock_session: MockDbSession):
    """Only the placeholder rows an invitation created are the meeting's to destroy; a real account
    outlives every meeting it joined."""
    meeting = create_meetup(id=1, title="Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    participant = create_user(id=2, tg_user_id=20, settings=create_settings(id=2))
    create_joined_link(user=participant, meetup=meeting, id=1)

    invitee_ids = await purge_meetups(mock_session, [meeting])

    assert invitee_ids == []
    # SQLAlchemy renders an empty IN as IN (NULL) AND (1 != 1).
    assert "DELETE FROM users WHERE users.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed


async def test_purge_meetups_names_the_invitees_it_destroys(mock_session: MockDbSession):
    """The invitee rows have no other trace, so the purge has to name them before they go."""
    meeting = create_meetup(id=1, title="Meeting")
    create_user(id=1, tg_user_id=10, owned_meetings=[meeting], settings=create_settings(id=1))
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=meeting, id=1)

    with capture_logs() as logs:
        await purge_meetups(mock_session, [meeting])

    purged = next(entry for entry in logs if entry["event"] == "Invitee users purged")
    assert (purged["count"], purged["user_ids"]) == (1, [3])
    assert purged["reason"] == "cascade_of_purged_meetups"


async def test_purge_meetups_without_meetings_deletes_nothing(mock_session: MockDbSession):
    assert await purge_meetups(mock_session, []) == []
    assert "DELETE FROM meetups WHERE meetups.id IN (NULL) AND (1 != 1)" in mock_session.queries_executed
