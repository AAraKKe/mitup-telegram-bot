from unittest import mock

import pytest
from sqlalchemy.exc import IntegrityError

from mitup_bot.handlers.meeting.utils import flush_new_participant
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from tests.helpers import MockDbSession, integrity_error


def test_flush_new_participant_returns_true_when_flush_succeeds(mock_session: MockDbSession):
    assert flush_new_participant(mock_session, mock.MagicMock(), mock.MagicMock()) is True
    mock_session.assert_flushed()


def test_flush_new_participant_swallows_uniqueness_clash(mock_session: MockDbSession):
    mock_session.flush.side_effect = integrity_error(JOINED_USERS_UNIQUE_CONSTRAINT)

    # The joined_users uniqueness violation is the expected concurrent-join clash: reported as a no-op.
    assert flush_new_participant(mock_session, mock.MagicMock(), mock.MagicMock()) is False


@pytest.mark.parametrize(
    "constraint_name",
    # An orig without diagnostics, and one naming a different constraint (e.g. a foreign key).
    [None, "joined_users_meetup_id_fkey"],
    ids=["no_diag", "different_constraint"],
)
def test_flush_new_participant_reraises_other_integrity_errors(
    mock_session: MockDbSession, constraint_name: str | None
):
    mock_session.flush.side_effect = integrity_error(constraint_name)

    # Only the uniqueness clash is a no-op; every other IntegrityError must surface.
    with pytest.raises(IntegrityError):
        flush_new_participant(mock_session, mock.MagicMock(), mock.MagicMock())
