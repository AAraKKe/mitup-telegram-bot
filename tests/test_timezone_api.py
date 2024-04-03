import logging
from unittest import mock

import googlemaps
import pytest
from pydantic import SecretStr

from mitup_bot import timezone_api
from mitup_bot.config import GoogleApiConfig
from mitup_bot.exceptions import (
    GeocodeClientAlreadyInitializedError,
    GeocodeClientNotConfiguredError,
    IncorrectCoordinatesError,
    IncorrectGeocodeKeyError,
    IncorrectKeyError,
    IncorrectTimezoneKeyError,
    TimezoneClientAlreadyInitializedError,
    TimezoneClientNotConfiguredError,
)

CONFIG_GMAPS_KEYS: GoogleApiConfig = GoogleApiConfig(
    gmaps_geocode_key=SecretStr("geo_key"),
    gmaps_timezone_key=SecretStr("timezone_key"),
)

COORDIDATES_FROM_LOCATION = [{"geometry": {"location": {"lat": 40.7128, "lng": -74.0059}}}]


@pytest.fixture
def gmaps_client():
    with mock.patch("mitup_bot.timezone_api.googlemaps.Client") as client:
        yield client


@pytest.fixture
def geocode_client():
    with mock.patch("mitup_bot.timezone_api.geocode_client") as client_factory_mock:
        client_mock = mock.MagicMock()
        client_factory_mock.return_value = client_mock
        yield client_mock


@pytest.fixture
def timezone_client():
    with mock.patch("mitup_bot.timezone_api.timezone_client") as client_factory_mock:
        client_mock = mock.MagicMock()
        client_factory_mock.return_value = client_mock
        yield client_mock


@pytest.fixture(autouse=True)
def reset_clients():
    yield
    timezone_api.__geocode_client = None  # type: ignore
    timezone_api.__timezone_client = None  # type: ignore


def test_configure_fails_with_incorrect_keys(gmaps_client):
    gmaps_client.side_effect = ValueError

    with pytest.raises(IncorrectKeyError):
        timezone_api.configure(CONFIG_GMAPS_KEYS)


def test_configure_with_already_initializated_geocode_client(gmaps_client):
    timezone_api.configure(CONFIG_GMAPS_KEYS)

    with pytest.raises(GeocodeClientAlreadyInitializedError):
        timezone_api.configure(CONFIG_GMAPS_KEYS)


def test_configure_with_already_initializated_timezone_client(gmaps_client):
    timezone_api.configure(CONFIG_GMAPS_KEYS)

    with pytest.raises(GeocodeClientAlreadyInitializedError):
        with pytest.raises(TimezoneClientAlreadyInitializedError):
            timezone_api.configure(CONFIG_GMAPS_KEYS)


def test_configure_success(gmaps_client):
    timezone_api.configure(CONFIG_GMAPS_KEYS)

    gmaps_client.call_args_list[0].assert_called_with(key="geo_key")
    gmaps_client.call_args_list[1].assert_called_with(key="timezone_key")


def test_get_timezone_by_address_success(
    geocode_client,
    timezone_client,
    caplog: pytest.LogCaptureFixture,
):
    geocode_client.geocode.return_value = COORDIDATES_FROM_LOCATION
    timezone_client.timezone.return_value = {"timeZoneId": "America/New_York"}

    timezone = timezone_api.get_timezone_by_address("New York")

    geocode_client.geocode.assert_called_once_with("New York")
    timezone_client.timezone.assert_called_once_with(
        (
            COORDIDATES_FROM_LOCATION[0]["geometry"]["location"]["lat"],
            COORDIDATES_FROM_LOCATION[0]["geometry"]["location"]["lng"],
        )
    )
    assert timezone == "America/New_York"
    assert not caplog.text


def test_get_timezone_by_address_raises_with_missing_geocode_client():
    with pytest.raises(GeocodeClientNotConfiguredError):
        timezone_api.get_timezone_by_address("New York")


def test_get_timezone_by_address_raises_with_missing_timezone_client(geocode_client):
    geocode_client.geocode.return_value = COORDIDATES_FROM_LOCATION

    with pytest.raises(TimezoneClientNotConfiguredError):
        timezone_api.get_timezone_by_address("New York")

    geocode_client.geocode.assert_called_once_with("New York")


def test_get_timezone_by_address_handles_geocode_failure(
    timezone_client, geocode_client, caplog: pytest.LogCaptureFixture
):
    geocode_client.geocode.return_value = []

    with caplog.at_level(logging.WARNING):
        result = timezone_api.get_timezone_by_address("New York")

    assert result is None
    assert "Could not retrieve timezone for address: New York" in caplog.text


def test_get_timezone_by_location_success(timezone_client):
    timezone_client.timezone.return_value = {"timeZoneId": "America/Los_Angeles"}

    timezone = timezone_api.get_timezone_by_location(34.0522, -118.2437)

    assert timezone == "America/Los_Angeles"


def test_get_timezone_by_location_raises_with_missing_timezone_client():
    with pytest.raises(TimezoneClientNotConfiguredError):
        timezone_api.get_timezone_by_location(34.0522, -118.2437)


def test_get_timezone_by_location_raise_incorrect_timezone_key_error(timezone_client):
    timezone_client.timezone.side_effect = googlemaps.exceptions.ApiError(404)

    with pytest.raises(IncorrectTimezoneKeyError):
        timezone_api.get_timezone_by_location(34.0522, -118.2437)


def test_get_coordinates_raise_incorrect_geocode_key_error(geocode_client):
    geocode_client.geocode.side_effect = googlemaps.exceptions.ApiError(404)

    with pytest.raises(IncorrectGeocodeKeyError):
        timezone_api.get_coordinates("New York")


def test_get_timezone_by_location_raises_incorrect_coordinates_error(timezone_client):
    timezone_client.timezone.return_value = None

    with pytest.raises(IncorrectCoordinatesError):
        timezone_api.get_timezone_by_location(34.0522, -118.2437)


def test_get_coordinates_raises_with_missing_geocode_client():
    with pytest.raises(GeocodeClientNotConfiguredError):
        timezone_api.get_coordinates("New York")


def test_get_coordinates_logs_warning_on_failure(geocode_client, caplog):
    caplog.set_level(logging.WARNING)
    geocode_client.geocode.return_value = []

    result = timezone_api.get_coordinates("Invalid address")

    assert "Could not retrieve coordinates for address: Invalid address" in caplog.text
    assert result is None
