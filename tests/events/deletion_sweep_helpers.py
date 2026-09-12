"""Fixtures-free helpers shared by the two meeting-deletion sweep test modules."""

import logging
from collections.abc import Sequence
from itertools import count
from unittest import mock

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import col
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot.events import deletion_sweep
from mitup_bot.models import Meetup, User
from tests.helpers import MockDbSession, create_meetup, create_settings, create_user


def register_nominated(
    mock_session: MockDbSession, statement: SelectOfScalar[Meetup], meetings: Sequence[Meetup]
) -> list[list[int]]:
    """Register *meetings* for both reads a sweep makes, and answer with its chunks of owner ids.

    A sweep reads its nomination once and then re-reads each chunk of owners under the transaction
    that marks or deletes them, so a meeting has to be registered for the chunk of its owner too.
    """
    mock_session.add_objects_with_statement(statement, tuple(meetings))
    chunks = deletion_sweep.owner_chunks(meetings)
    for owner_ids in chunks:
        chunk = tuple(meeting for meeting in meetings if meeting.owner.db_id in owner_ids)
        mock_session.add_objects_with_statement(statement.where(col(User.id).in_(owner_ids)), chunk)
    return chunks


def transaction_count(mock_session: MockDbSession) -> int:
    """How many transactions the run opened: one per `db.begin()`, which is one per commit."""
    return mock_session.begin.call_count


def fail_transaction(mock_session: MockDbSession, number: int, *, on_commit: bool = False):
    """Break the *number*-th transaction of the run the way a lost connection does.

    `on_commit` fails it at the end instead of the start, the shape that loses marks for digests
    that already went out.
    """
    lost = OperationalError("SELECT 1", {}, ConnectionError("connection lost"))
    transactions = count(1)

    def break_nth(*_args, **_kwargs):
        if next(transactions) == number:
            raise lost
        return mock.DEFAULT

    if on_commit:
        mock_session.begin.return_value.__aexit__.side_effect = break_nth
    else:
        mock_session.begin.side_effect = break_nth


def residue_record(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    """The residue warning line for *event*; WARNING capture also picks up unrelated framework
    lines, so the lookup filters by the structlog event string (the LogRecord message)."""
    return next(record for record in caplog.records if record.message == event)


def owned_meetups(owner_id: int, count: int, *, first_meeting_id: int) -> list[Meetup]:
    """*count* meetings of one owner, numbered from *first_meeting_id*."""
    meetings = [create_meetup(id=first_meeting_id + index, title=f"Meeting {index}") for index in range(count)]
    create_user(
        id=owner_id,
        tg_user_id=owner_id * 10,
        owned_meetings=meetings,
        settings=create_settings(id=owner_id),
    )
    return meetings


def owners_with_one_meeting(owners: int) -> list[Meetup]:
    """One nominated meeting for each of *owners* owners, numbered from 1."""
    return [owned_meetups(owner_id, 1, first_meeting_id=owner_id)[0] for owner_id in range(1, owners + 1)]


def over_one_chunk() -> list[Meetup]:
    """Enough owners for three chunks, derived so a retuned chunk size keeps the intended shape."""
    return owners_with_one_meeting(2 * deletion_sweep.OWNERS_PER_CHUNK + 1)
