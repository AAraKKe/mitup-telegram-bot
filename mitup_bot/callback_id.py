from enum import Enum


class CallbackId(Enum):
    """
    CallbackID is an Enum that is used to identify the different handlers that are registered in the bot via the
    registry.

    It is supposed to be subclasses, with each subclass of CallbackId represents a semantic group of handlers.
    """

    @property
    def value(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"

    def __repr__(self) -> str:
        return self.value
