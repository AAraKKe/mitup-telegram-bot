from collections.abc import Mapping
from typing import Any, Protocol
from unittest import mock

from click.testing import Result
from telegram.ext import Application

from mitup_bot.custom_context import MitupContext, MitupUserData

from .api import MockApi
from .monitoring import StubMetricsEngine

StubMitupContext = MitupContext[mock.MagicMock, MockApi, StubMetricsEngine]
"""MitupContext type for testing purposes"""

StubMitupApp = Application[mock.MagicMock, StubMitupContext, MitupUserData, dict, dict, None]
"""Application type for testing purposes"""


class AnyFloat(float):
    """Use this in assertions for metrics where the value is not important"""

    def __eq__(self, other: Any) -> bool:
        return True


class CliRunner(Protocol):
    def __call__(
        self, args: str | None = None, input: str | None = None, env: Mapping[str, str] | None = None
    ) -> Result: ...
