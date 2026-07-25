from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from click.testing import Result
from telegram.ext import Application

from mitup_bot.custom_context import MitupContext, MitupUserData

from .api import MockApi
from .stub_bot import StubBot

if TYPE_CHECKING:  # pragma: no cover
    from mitup_bot.config import BotConfig
    from mitup_bot.models import Broadcast, Meetup, Message, User

StubMitupContext = MitupContext[StubBot, MockApi]
"""MitupContext type for testing purposes"""

StubMitupApp = Application[StubBot, StubMitupContext, MitupUserData, dict, dict, None]
"""Application type for testing purposes"""


class AnyFloat(float):
    """Use this in assertions for metrics where the value is not important"""

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, (int, float))

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)


class CliRunner(Protocol):
    def __call__(
        self, args: str | None = None, input: str | None = None, env: Mapping[str, str] | None = None
    ) -> Result: ...


class RegisterMember(Protocol):
    """Factory-fixture callable that seeds the `guards.member_user` query with a MEMBER row."""

    def __call__(self, user: User) -> None: ...


class RegisterAuthorDrafts(Protocol):
    """Factory-fixture callable that seeds the draft-sweep query with an author's DRAFT broadcasts."""

    def __call__(self, author_tg_id: int, drafts: tuple[Broadcast, ...]) -> None: ...


class ClaimSharedCard(Protocol):
    """Factory-fixture callable that tracks a meeting card as shared through inline mode.

    Reproduces what the chosen-inline-result handler records when a user picks the card, which is
    what authorizes later interactions with it.
    """

    def __call__(self, meeting: Meetup, inline_message_id: str = ...) -> Message: ...


class StashBotConfig(Protocol):
    """Factory-fixture callable that stashes a `BotConfig` into the app's bot_data."""

    def __call__(self, config: BotConfig) -> None: ...
