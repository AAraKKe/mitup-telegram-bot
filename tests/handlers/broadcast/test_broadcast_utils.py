from sqlalchemy import func
from sqlmodel import col, select

from mitup_bot.handlers.broadcast.utils import count_members_by_language
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
