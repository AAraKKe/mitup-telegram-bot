"""This module contains custom types to be used through the project for type hinting"""

from collections.abc import Callable, Collection, Coroutine
from types import FunctionType
from typing import TYPE_CHECKING, Any, TypeVar

from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot, JobQueue

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.custom_context import MitupContext, MitupUserData

T = TypeVar("T")

RT = TypeVar("RT")
"""References the return type of a handler callback"""

OneOrMany = T | Collection[T]
"""Type that defines one or a collection of T objects"""

CCT = TypeVar("CCT", bound=CallbackContext[Any, Any, Any, Any])
"""Type that refers to a subclass of CallbackContext"""

UT = TypeVar("UT", bound=Update)
"""Type that defines and Update objects or a subclass of it"""


TMitupContext = MitupContext[ExtBot, TelegramApiWrapper]
"""Standard type of MitupContext used through the project"""

JQ = TypeVar("JQ", bound=JobQueue | None)
MitupApp = Application[ExtBot, TMitupContext, MitupUserData, dict, dict, JQ]
"""Standard application type for the MitupBot"""


if TYPE_CHECKING:
    from ty_extensions import Intersection

    # Intersection is only available during type checking
    HandlerCallback = Intersection[Callable[[Update, MitupContext], Coroutine[Any, Any, RT]], FunctionType]
    """Type to define the callback of a given handler"""
else:
    HandlerCallback = Callable[[Update, MitupContext], Coroutine[Any, Any, RT]]
