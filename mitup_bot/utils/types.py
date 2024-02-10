"""This module contains custom types to be used through the project for type hinting"""

from collections.abc import Callable, Collection, Coroutine
from typing import Any, TypeVar, Union

from telegram import Update
from telegram.ext import CallbackContext

T = TypeVar("T")

RT = TypeVar("RT")
"""References the return type of a handler callback"""

OneOrMany = Union[T, Collection[T]]
"""Type that defines one or a collection of T objects"""

CCT = TypeVar("CCT", bound=CallbackContext[Any, Any, Any, Any])
"""Type that refers to a subclass of CallbackContext"""

UT = TypeVar("UT", bound=Update)
"""Type that defines and Update objects or a subclass of it"""

HandlerCallback = Callable[[UT, CCT], Coroutine[Any, Any, T]]
"""Type to define the callback of a given handler"""
