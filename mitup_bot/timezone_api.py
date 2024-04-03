import logging

import googlemaps
from googlemaps import Client

from mitup_bot.config import GoogleApiConfig

from .exceptions import (
    GeocodeClientAlreadyInitializedError,
    GeocodeClientNotConfiguredError,
    IncorrectCoordinatesError,
    IncorrectGeocodeKeyError,
    IncorrectKeyError,
    IncorrectTimezoneKeyError,
    TimezoneClientAlreadyInitializedError,
    TimezoneClientNotConfiguredError,
)

__geocode_client: googlemaps.Client | None = None
__timezone_client: googlemaps.Client | None = None


def geocode_client() -> Client:
    if __geocode_client is None:
        raise GeocodeClientNotConfiguredError()
    return __geocode_client


def timezone_client() -> Client:
    if __timezone_client is None:
        raise TimezoneClientNotConfiguredError()
    return __timezone_client


def configure(config: GoogleApiConfig):
    """
    Configures the geocode and timezone clients with the provided API keys.

    Args:
        geocode_key (SecretStr): The API key for the geocode client.
        timezone_key (SecretStr): The API key for the timezone client.
    """
    global __geocode_client, __timezone_client

    if __geocode_client:
        raise GeocodeClientAlreadyInitializedError()
    if __timezone_client:
        raise TimezoneClientAlreadyInitializedError()

    try:
        __geocode_client = googlemaps.Client(key=config.gmaps_geocode_key.get_secret_value())
        __timezone_client = googlemaps.Client(key=config.gmaps_timezone_key.get_secret_value())
    except ValueError as e:
        raise IncorrectKeyError() from e


def get_timezone_by_address(address: str) -> str | None:
    """
    Retrieves the timezone ID for a given address.

    Args:
        address (str): The address for which to retrieve the timezone.

    Returns:
        str: The timezone ID.

    """

    if (location := get_coordinates(address)) and (
        timezone := get_timezone_by_location(location.get("lat"), location.get("lng"))  # type: ignore
    ):
        return timezone

    logging.warning(f"Could not retrieve timezone for address: {address}")

    return None


def get_timezone_by_location(latitude: float, longitude: float) -> str:
    """
    Get the timezone by location coordinates.

    Args:
        latitude (float): The latitude of the location.
        longitude (float): The longitude of the location.

    Returns:
        str: The timezone ID of the location.
    """

    try:
        timezone = timezone_client().timezone((latitude, longitude))  # type: ignore
    except googlemaps.exceptions.ApiError as e:
        raise IncorrectTimezoneKeyError() from e

    if timezone is None:
        raise IncorrectCoordinatesError()

    return timezone["timeZoneId"]


def get_coordinates(address: str) -> dict[str, float] | None:
    """
    Retrieves the coordinates (latitude and longitude) of a given address.

    Args:
        address (str): The address to retrieve the coordinates for.

    Returns:
        dict[str, float]: A dictionary containing the latitude and longitude coordinates.
    """

    try:
        if geocode_result := geocode_client().geocode(address):  # type: ignore
            return geocode_result[0]["geometry"]["location"]
    except googlemaps.exceptions.ApiError as e:
        raise IncorrectGeocodeKeyError() from e

    logging.warning(f"Could not retrieve coordinates for address: {address}")

    return None
