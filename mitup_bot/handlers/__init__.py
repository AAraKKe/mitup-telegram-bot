# Export all handlers available to make sure we register them
__all__ = (
    "commands",
    "HandlersRegistry",
)

from . import commands
from .registry import HandlersRegistry
