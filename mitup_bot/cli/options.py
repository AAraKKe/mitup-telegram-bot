from enum import StrEnum, auto
from typing import Generic, TypeVar

from click import Choice
from click.core import Context, Parameter


class Env(StrEnum):
    DEV = auto()
    PROD = auto()
    # Sample environment to ensure config values even though
    # they are not real
    SAMPLE = auto()


T = TypeVar("T", bound=StrEnum)


class EnumChoice(Generic[T], Choice):
    """This is a custom click choice that allows passing an enum for the allowed choices
    instead of passing a list of strings.

    This allows keeping all valid choices in an Enum class at the same time as providing
    a parameter that can be used as the Enum it is. Internally Click will treat these options as
    strings and also presented to the user as strings but internally we will convert them to
    the Enum they represent
    """

    name = "enum_choice"

    def __init__(self, choices: type[T]) -> None:
        """Define the choices as the valid values of the Enum supplied"""
        self.enum_type = choices
        valid_choices = [choice.value.lower() for choice in choices]
        super().__init__(valid_choices, case_sensitive=False)

    def convert(self, value: str, param: Parameter | None, ctx: Context | None) -> T:
        converted_str: str = super().convert(value, param, ctx).upper()

        return self.enum_type[converted_str]
