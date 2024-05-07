from enum import Enum
from functools import cached_property, reduce
from typing import override


class CallbackId(Enum):
    """
    CallbackID is an Enum that is used to identify the different handlers that are registered in the bot via the
    registry.

    It is supposed to be subclasses, with each subclass of CallbackId represents a semantic group of handlers.
    """

    @property
    @override
    def value(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"

    @override
    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return self.value

    @cached_property
    def dimension(self) -> str:
        """
        The value that this CallbackId is represented by when used in metrics dimensions.

        This is usually the value of the enum removing CallbackId or HandlerId and transformed into
        camel case.

        For example: EditMeetingHandlerId.LOCATION_NAME_CALLBACK -> EditMeetingLocationNameCallback
        """
        camel_case = f"{self.__class__.__name__}_{self.name.title().replace('_', '')}"
        # Order matters here
        to_remove = ["CallbackId", "CallbackQueryId", "HandlerId", "Handler", "CommandsId", "_"]
        return reduce(lambda a, b: a.replace(b, ""), to_remove, camel_case)
