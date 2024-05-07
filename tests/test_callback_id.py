from enum import auto

import pytest

from mitup_bot.callback_id import CallbackId
from mitup_bot.handlers.commands import CommandsId


class MyCallbackId(CallbackId):
    SOMETHING = auto()
    SOMETHING_ELSE = auto()


class MyHandlerId(CallbackId):
    LOCATION_NAME = auto()
    LOCATION_COORDINATES = auto()


class MyHandler(CallbackId):
    LOCATION_NAME = auto()
    LOCATION_COORDINATES = auto()


class NoKeywords(CallbackId):
    SINGLE = auto()
    MULTIPLE_PARTS = auto()
    CALLBACK = auto()


@pytest.mark.parametrize(
    "callback_id, expected",
    [
        (MyCallbackId.SOMETHING, "MySomething"),
        (MyCallbackId.SOMETHING_ELSE, "MySomethingElse"),
        (MyHandlerId.LOCATION_NAME, "MyLocationName"),
        (MyHandlerId.LOCATION_COORDINATES, "MyLocationCoordinates"),
        (MyHandler.LOCATION_NAME, "MyLocationName"),
        (NoKeywords.SINGLE, "NoKeywordsSingle"),
        (NoKeywords.MULTIPLE_PARTS, "NoKeywordsMultipleParts"),
        (NoKeywords.CALLBACK, "NoKeywordsCallback"),
        (CommandsId.MAIN_MENU, "MainMenu"),
    ],
    ids=[
        "MyCallbackId.SOMETHING",
        "MyCallbackId.SOMETHING_ELSE",
        "MyHandlerId.LOCATION_NAME",
        "MyHandlerId.LOCATION_COORDINATES",
        "MyHandler.LOCATION_NAME",
        "NoKeywords.SINGLE",
        "NoKeywords.MULTIPLE_PARTS",
        "NoKeywords.CALLBACK",
        "CommandsId.MAIN_MENU",
    ],
)
def test_callback_id_dimension(callback_id: CallbackId, expected: CallbackId):
    assert callback_id.dimension == expected


@pytest.mark.parametrize(
    "callback_id, expected",
    [
        (MyCallbackId.SOMETHING, "MyCallbackId.SOMETHING"),
        (MyCallbackId.SOMETHING_ELSE, "MyCallbackId.SOMETHING_ELSE"),
        (MyHandlerId.LOCATION_NAME, "MyHandlerId.LOCATION_NAME"),
    ],
    ids=[
        "MyCallbackId.SOMETHING",
        "MyCallbackId.SOMETHING_ELSE",
        "MyHandlerId.LOCATION_NAME",
    ],
)
def test_callback_id_value(callback_id: CallbackId, expected: CallbackId):
    assert callback_id.value == expected
