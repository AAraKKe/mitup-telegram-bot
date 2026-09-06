import pytest
from sqlalchemy import func
from sqlmodel import col, select

from mitup_bot.handlers.broadcast.utils import BROADCAST_NAME_MAX_LENGTH, count_members_by_language, derive_name
from mitup_bot.models import Settings, User
from mitup_bot.models.users import UserStatus
from tests.helpers import MockDbSession


async def test_count_members_by_language_groups_rows(mock_session: MockDbSession):
    # Reconstruct the exact grouped-count statement the helper issues so the mock returns the rows.
    statement = (
        select(Settings.language, func.count())
        .join(User, onclause=col(User.id) == col(Settings.user_id))
        .where(col(User.status) == UserStatus.MEMBER)
        .group_by(col(Settings.language))
    )
    mock_session.add_objects_with_statement(statement, (("en", 3), ("es_ES", 2)))

    counts = await count_members_by_language(mock_session)

    assert counts == {"en": 3, "es_ES": 2}


@pytest.mark.parametrize(
    "body, expected",
    [
        pytest.param("<h2>Release 2.0</h2>\n\nWe shipped a lot.", "Release 2.0", id="heading"),
        pytest.param("<b>Release 2.0</b>\n\nbody", "Release 2.0", id="bold"),
        pytest.param("<ul><li>Release 2.0</li><li>and more</li></ul>", "Release 2.0", id="list_item"),
        pytest.param("Release   2.0  is out", "Release 2.0 is out", id="collapsed_spaces"),
        pytest.param("<h2></h2>\n\nRelease 2.0", "Release 2.0", id="empty_first_line_skipped"),
        pytest.param("   \n", "Broadcast", id="nothing_to_name_it_after"),
    ],
)
def test_derive_name_takes_the_first_line_of_the_body_without_its_tags(body: str, expected: str):
    assert derive_name(body) == expected


def test_derive_name_truncates_a_long_first_line():
    assert derive_name("a" * (BROADCAST_NAME_MAX_LENGTH + 10)) == "a" * BROADCAST_NAME_MAX_LENGTH
