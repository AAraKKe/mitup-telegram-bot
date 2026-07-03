from tests.helpers.stub_db import MockDbSession


def assert_locked_meetup_select(mock_session: MockDbSession):
    """Assert the handler loaded the meeting under the per-meeting row lock (issue #187).

    Deliberately tolerant of dialect/formatting details: every executed meetups SELECT must carry
    FOR UPDATE. Flows that legitimately pre-read the meeting unlocked (e.g. earlier conversation
    steps) should reset ``mock_session.exec`` before the mutating step so only its queries remain.
    """
    meetup_selects = [
        query
        for query in mock_session.queries_executed
        if query.lower().startswith("select") and "from meetups" in query.lower()
    ]
    assert meetup_selects, "expected the handler to SELECT the meeting from the meetups table"
    unlocked = [query for query in meetup_selects if "FOR UPDATE" not in query]
    assert not unlocked, f"meetups SELECT(s) missing FOR UPDATE: {unlocked}"
