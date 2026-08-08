from unittest import mock

import pytest
from sqlmodel import col, func, select

from mitup_bot.custom_context import BOT_CONFIG_KEY
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from tests.helpers import MockDbSession, StubMitupApp, create_bot_config
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.types import RegisterGrantTarget, RegisterMember


@pytest.fixture(autouse=True)
def default_admin_config(app: StubMitupApp):
    """Every grant handler is admin-gated, so default the acting user (DEFAULT_USER_ID) onto the
    allowlist and let each test exercise the handler body. Tests covering the non-admin drop stash
    a config that excludes the sender."""
    app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])


@pytest.fixture
def register_member(mock_session: MockDbSession) -> RegisterMember:
    """Register the exact query `guards.member_user` issues so the operator resolves as a MEMBER."""

    def register(user: User):
        statement = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
        mock_session.add_objects_with_statement(statement, (user,))

    return register


@pytest.fixture
def register_target(mock_session: MockDbSession) -> RegisterGrantTarget:
    """Register the exact queries `find_target` issues, so the row resolves by numeric id and by
    username, plus the primary-key lookup the callback handlers re-load the row with."""

    def register(target: User):
        by_id = select(User).where(col(User.tg_user_id) == target.tg_user_id, User.status == UserStatus.MEMBER)
        mock_session.add_objects_with_statement(by_id, (target,))
        if target.username is not None:
            by_username = select(User).where(
                func.lower(col(User.username)) == target.username.lower(), User.status == UserStatus.MEMBER
            )
            mock_session.add_objects_with_statement(by_username, (target,))
        # MockDbSession has no primary-key registry for `session.get`; stub it directly, as the
        # callback handlers re-load the row that way.
        mock_session.get = mock.AsyncMock(return_value=target)

    return register
