from enum import StrEnum

import click
import pytest

from mitup_bot.cli import options


class TestEnumOptions(StrEnum):
    OPT1 = "opt1"
    OPT2 = "opt2"


def test_enum_choice_returns_enum():
    choice = options.EnumChoice(TestEnumOptions)

    assert choice.convert("opt1", None, None) is TestEnumOptions.OPT1
    assert choice.convert("opt2", None, None) is TestEnumOptions.OPT2


def test_enum_choice_fails_with_invalid_input():
    choice = options.EnumChoice(TestEnumOptions)

    with pytest.raises(click.BadParameter):
        choice.convert("something", None, None)
