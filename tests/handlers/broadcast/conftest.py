import pytest
from sqlmodel import col, select

from mitup_bot.config import BotConfig
from mitup_bot.handlers.broadcast.utils import BOT_CONFIG_KEY
from mitup_bot.models import Broadcast, User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.models.users import UserStatus
from tests.helpers import MockDbSession, StubMitupApp
from tests.helpers.types import RegisterAuthorDrafts, RegisterMember, StashBotConfig


@pytest.fixture
def register_member(mock_session: MockDbSession) -> RegisterMember:
    """Register the exact query `guards.member_user` issues so the operator resolves as a MEMBER."""

    def register(user: User) -> None:
        statement = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
        mock_session.add_objects_with_statement(statement, (user,))

    return register


@pytest.fixture
def register_author_drafts(mock_session: MockDbSession) -> RegisterAuthorDrafts:
    """Register the exact query `discard_author_drafts` sweeps so the draft cleanup finds them."""

    def register(author_tg_id: int, drafts: tuple[Broadcast, ...]) -> None:
        statement = select(Broadcast).where(
            col(Broadcast.author_tg_id) == author_tg_id, col(Broadcast.status) == BroadcastStatus.DRAFT
        )
        mock_session.add_objects_with_statement(statement, drafts)

    return register


@pytest.fixture
def stash_bot_config(app: StubMitupApp) -> StashBotConfig:
    """Mirror MitupRuntime: the guard reads BotConfig from bot_data under a fixed key."""

    def stash(config: BotConfig) -> None:
        app.bot_data[BOT_CONFIG_KEY] = config

    return stash
