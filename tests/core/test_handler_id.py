from enum import auto

import pytest

from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.commands import CommandsId


class MyCallbackId(HandlerId):
    SOMETHING = auto()
    SOMETHING_ELSE = auto()


class MyHandlerId(HandlerId):
    LOCATION_NAME = auto()
    LOCATION_COORDINATES = auto()


class MyHandler(HandlerId):
    LOCATION_NAME = auto()
    LOCATION_COORDINATES = auto()


class NoKeywords(HandlerId):
    SINGLE = auto()
    MULTIPLE_PARTS = auto()
    CALLBACK = auto()


@pytest.mark.parametrize(
    "handler_id, expected",
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
def test_handler_id_dimension(handler_id: HandlerId, expected: HandlerId):
    assert handler_id.dimension == expected


@pytest.mark.parametrize(
    "handler_id, expected",
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
def test_handler_id_value(handler_id: HandlerId, expected: HandlerId):
    assert handler_id.value == expected
