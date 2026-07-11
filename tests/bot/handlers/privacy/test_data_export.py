import datetime as dt
import json

from mitup_bot.handlers.privacy import data_export
from mitup_bot.models import User
from tests.helpers.fixtures import (
    create_joined_link,
    create_meetup,
    create_member,
    create_supporter_subscription,
    create_user,
)
from tests.helpers.stub_db import MockDbSession

EXPORTER_TG_USER_ID = 111_222_333
FRIEND_TG_USER_ID = 987_654_321
FRIEND_DB_ID = 555_444
WAITER_TG_USER_ID = 876_543_210
WAITER_DB_ID = 666_555
ORGANIZER_TG_USER_ID = 765_432_109
ORGANIZER_DB_ID = 777_888


def seed_exporter_with_social_graph(mock_session: MockDbSession) -> User:
    """An exporter owning a meeting (one participant, one on the waiting list) who has
    also joined someone else's meeting. Every other user's ids are distinctive numbers
    so the leak assertions cannot collide with dates or counters in the JSON."""
    own_meeting = create_meetup(
        id=10,
        title="Own Meeting",
        description="A meeting I own",
        datetime=dt.datetime(2026, 8, 1, 18, 0, tzinfo=dt.UTC),
        waiting_list=True,
        max_members=1,
    )
    exporter = create_user(
        id=1, tg_user_id=EXPORTER_TG_USER_ID, username="exporter", last_name="Doe", owned_meetings=[own_meeting]
    )
    friend = create_member(id=FRIEND_DB_ID, tg_user_id=FRIEND_TG_USER_ID)
    friend.first_name = "Friend Person"
    waiter = create_member(id=WAITER_DB_ID, tg_user_id=WAITER_TG_USER_ID)
    waiter.first_name = "Waiting Person"
    organizer = create_member(id=ORGANIZER_DB_ID, tg_user_id=ORGANIZER_TG_USER_ID)
    organizer.first_name = "Organizer Person"
    create_joined_link(friend, own_meeting)
    create_joined_link(waiter, own_meeting, is_waiting_list=True)

    other_meeting = create_meetup(
        id=20,
        title="Other Meeting",
        datetime=dt.datetime(2026, 9, 1, 20, 0, tzinfo=dt.UTC),
        owner=organizer,
    )
    exporter_link = create_joined_link(exporter, other_meeting)

    mock_session.add_objects_with_statement(data_export.owned_meetings_statement(exporter), (own_meeting,))
    mock_session.add_objects_with_statement(data_export.joined_links_statement(exporter), (exporter_link,))
    return exporter


async def test_export_contains_the_users_own_record_and_settings(mock_session: MockDbSession):
    exporter = seed_exporter_with_social_graph(mock_session)

    export = await data_export.build_user_export(mock_session, exporter)

    assert export["bot"] == "Mitup"
    assert export["exported_at"] is not None
    assert export["user"]["telegram_user_id"] == EXPORTER_TG_USER_ID
    assert export["user"]["first_name"] == exporter.first_name
    assert export["user"]["last_name"] == "Doe"
    assert export["user"]["username"] == "exporter"
    assert export["user"]["status"] == "member"
    assert export["settings"]["language"] == exporter.settings.language
    assert export["settings"]["timezone"] == exporter.settings.timezone


async def test_export_lists_owned_meetings_with_participants_as_display_names(mock_session: MockDbSession):
    exporter = seed_exporter_with_social_graph(mock_session)

    export = await data_export.build_user_export(mock_session, exporter)

    own_meeting = export["meetings"][0]
    assert [meeting["title"] for meeting in export["meetings"]] == ["Own Meeting"]
    assert own_meeting["description"] == "A meeting I own"
    assert own_meeting["start_datetime"] == "2026-08-01T18:00:00+00:00"
    assert own_meeting["max_participants"] == 1
    assert own_meeting["participants"] == ["Friend Person"]
    assert own_meeting["waiting_list"] == ["Waiting Person"]


async def test_export_lists_joined_meetings_with_the_organizer_display_name(mock_session: MockDbSession):
    exporter = seed_exporter_with_social_graph(mock_session)

    export = await data_export.build_user_export(mock_session, exporter)

    assert [joined["meeting_title"] for joined in export["joined_meetings"]] == ["Other Meeting"]
    joined = export["joined_meetings"][0]
    assert joined["organizer"] == "Organizer Person"
    assert joined["meeting_start_datetime"] == "2026-09-01T20:00:00+00:00"
    assert joined["on_waiting_list"] is False


async def test_export_never_leaks_other_users_ids(mock_session: MockDbSession):
    exporter = seed_exporter_with_social_graph(mock_session)

    export = await data_export.build_user_export(mock_session, exporter)

    serialized = json.dumps(export)
    for other_user_number in (
        FRIEND_TG_USER_ID,
        FRIEND_DB_ID,
        WAITER_TG_USER_ID,
        WAITER_DB_ID,
        ORGANIZER_TG_USER_ID,
        ORGANIZER_DB_ID,
    ):
        assert str(other_user_number) not in serialized
    # The exporting user's own Telegram id is their data and stays in.
    assert str(EXPORTER_TG_USER_ID) in serialized


async def test_export_includes_the_patreon_link_when_present(mock_session: MockDbSession):
    exporter = create_user(id=1, tg_user_id=EXPORTER_TG_USER_ID)
    subscription = create_supporter_subscription(
        user_id=1,
        patreon_user_id="patreon-abc",
        support_expiration=dt.datetime(2026, 12, 31, tzinfo=dt.UTC),
    )
    mock_session.add_object(subscription, "user_id")

    export = await data_export.build_user_export(mock_session, exporter)

    assert export["patreon"]["patreon_user_id"] == "patreon-abc"
    assert export["patreon"]["support_expiration"] == "2026-12-31T00:00:00+00:00"


async def test_export_omits_joins_to_the_users_own_meetings(mock_session: MockDbSession):
    own_meeting = create_meetup(id=10, title="Own Meeting")
    exporter = create_user(id=1, tg_user_id=EXPORTER_TG_USER_ID, owned_meetings=[own_meeting])
    own_link = create_joined_link(exporter, own_meeting)
    mock_session.add_objects_with_statement(data_export.owned_meetings_statement(exporter), (own_meeting,))
    mock_session.add_objects_with_statement(data_export.joined_links_statement(exporter), (own_link,))

    export = await data_export.build_user_export(mock_session, exporter)

    assert export["joined_meetings"] == []
    assert export["meetings"][0]["participants"] == [exporter.display_name]


async def test_export_for_a_user_with_no_activity(mock_session: MockDbSession):
    exporter = create_user(id=1, tg_user_id=EXPORTER_TG_USER_ID)

    export = await data_export.build_user_export(mock_session, exporter)

    assert export["meetings"] == []
    assert export["joined_meetings"] == []
    assert export["patreon"] is None
    document, _ = data_export.export_document(export)
    assert json.loads(document) == export


def test_export_document_filename_carries_the_utc_date():
    document, filename = data_export.export_document({"bot": "Mitup"})

    assert filename == f"mitup-export-{dt.datetime.now(dt.UTC):%Y-%m-%d}.json"
    assert json.loads(document) == {"bot": "Mitup"}


def test_iso_utc_normalizes_every_datetime_shape():
    naive_utc = dt.datetime(2026, 7, 9, 12, 30)
    madrid = dt.datetime(2026, 7, 9, 14, 30, tzinfo=dt.timezone(dt.timedelta(hours=2)))

    assert data_export.iso_utc(naive_utc) == "2026-07-09T12:30:00+00:00"
    assert data_export.iso_utc(madrid) == "2026-07-09T12:30:00+00:00"
    assert data_export.iso_utc(None) is None
