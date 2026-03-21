from collections.abc import Mapping
from typing import Any, Protocol

from click.testing import Result
from telegram.ext import Application

from mitup_bot.custom_context import MitupContext, MitupUserData

from .api import MockApi
from .monitoring import StubMetricsEngine
from .stub_bot import StubBot

StubMitupContext = MitupContext[StubBot, MockApi, StubMetricsEngine]
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
