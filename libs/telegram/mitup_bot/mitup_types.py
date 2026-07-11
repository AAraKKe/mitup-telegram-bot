"""This module contains custom types to be used through the project for type hinting"""

from collections.abc import Callable, Collection, Coroutine
from types import FunctionType
from typing import TYPE_CHECKING, Any, TypeVar

from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot, JobQueue

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.custom_context import TAPI, TB, MitupContext, MitupUserData

T = TypeVar("T")

RT = TypeVar("RT")
"""References the return type of a handler callback"""

OneOrMany = T | Collection[T]
"""Type that defines one or a collection of T objects"""

CCT = TypeVar("CCT", bound=CallbackContext[Any, Any, Any, Any])
"""Type that refers to a subclass of CallbackContext"""

UT = TypeVar("UT", bound=Update)
"""Type that defines and Update objects or a subclass of it"""


TMitupContext = MitupContext[TB, TAPI]
"""Polymorphic MitupContext alias used in handler / utility signatures.

Because `TB` and `TAPI` are free type variables (bounded by `ExtBot` and
`TelegramApiWrapper` respectively), any function that takes `context: TMitupContext`
is implicitly generic in those parameters. This lets prod call sites pass
`MitupContext[ExtBot, TelegramApiWrapper]` and test call sites pass
`MitupContext[StubBot, MockApi]` to the same function.
"""

JQ = TypeVar("JQ", bound=JobQueue | None)
MitupApp = Application[ExtBot, MitupContext[ExtBot, TelegramApiWrapper], MitupUserData, dict, dict, JQ]
"""Standard application type for the MitupBot — concrete prod parameterization."""


if TYPE_CHECKING:
    from ty_extensions import Intersection

    # Intersection is only available during type checking
    HandlerCallback = Intersection[Callable[[Update, MitupContext], Coroutine[Any, Any, RT]], FunctionType]
    """Type to define the callback of a given handler"""
else:
    HandlerCallback = Callable[[Update, MitupContext], Coroutine[Any, Any, RT]]
