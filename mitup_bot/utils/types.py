"""This module contains custom types to be used through the project for type hinting"""

from collections.abc import Callable, Collection, Coroutine
from typing import Any, Protocol, TypeVar, Union

from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from telegram import Update
from telegram.ext import Application, CallbackContext, ExtBot
from telegram.ext._utils.types import JQ

from mitup_bot.custom_context import MitupContext, MitupUserData

T = TypeVar("T")

RT = TypeVar("RT")
"""References the return type of a handler callback"""

OneOrMany = Union[T, Collection[T]]  # noqa: UP007
"""Type that defines one or a collection of T objects"""

CCT = TypeVar("CCT", bound=CallbackContext[Any, Any, Any, Any])
"""Type that refers to a subclass of CallbackContext"""

UT = TypeVar("UT", bound=Update)
"""Type that defines and Update objects or a subclass of it"""

HandlerCallback = Callable[[Update, MitupContext], Coroutine[Any, Any, RT]]
"""Type to define the callback of a given handler"""


MitupApp = Application[ExtBot, MitupContext[ExtBot, MetricsLogger], MitupUserData, dict, dict, JQ]
"""Standard application type for the MitupBot"""


class ContextManager[T](Protocol):
    def __enter__(self) -> T: ...

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None: ...
