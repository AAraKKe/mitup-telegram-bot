import googlemaps
import structlog
from googlemaps import Client
from pydantic import BaseModel, ValidationError

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
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import MetricKey

log = structlog.get_logger(__name__)

__geocode_client: googlemaps.Client | None = None
__timezone_client: googlemaps.Client | None = None


class GeocodingLocation(BaseModel):
    lat: float
    lng: float


class GeocodingGeometry(BaseModel):
    location: GeocodingLocation


class GeocodingResponse(BaseModel):
    geometry: GeocodingGeometry


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


def get_timezone_by_address(address: str, context: TMitupContext) -> str | None:
    """
    Retrieves the timezone ID for a given address.

    Args:
        address (str): The address for which to retrieve the timezone.

    Returns:
        str: The timezone ID.

    """

    location = get_coordinates(address, context)

    return get_timezone_by_location(location.lat, location.lng, context)


def get_timezone_by_location(latitude: float, longitude: float, context: TMitupContext) -> str:
    """
    Get the timezone by location coordinates.

    Args:
        latitude (float): The latitude of the location.
        longitude (float): The longitude of the location.

    Returns:
        str: The timezone ID of the location.
    """

    try:
        with context.with_time_metric("GoogleTimeZoneApi"):
            timezone = timezone_client().timezone((latitude, longitude))  # type: ignore
    except googlemaps.exceptions.ApiError as e:
        # Covers any Google-side rejection — quota, auth, upstream 5xx — not only a bad key; the
        # log line preserves what Google actually answered.
        log.warning("Google timezone API call failed", exc_info=e)
        raise IncorrectTimezoneKeyError() from e

    if timezone is None:
        context.emit_metric(
            MetricKey.ERROR.with_prefix("InvalidGoogleTimezoneResponse"), include_handler_properties=False
        )
        raise IncorrectCoordinatesError()

    return timezone["timeZoneId"]


def get_coordinates(address: str, context: TMitupContext) -> GeocodingLocation:
    """
    Retrieves the coordinates (latitude and longitude) of a given address.

    Args:
        address (str): The address to retrieve the coordinates for.

    Returns:
        dict[str, float]: A dictionary containing the latitude and longitude coordinates.
    """

    try:
        with context.with_time_metric("GoogleGeocodeApi"):
            geocode_result = GeocodingResponse.model_validate(
                geocode_client().geocode(address)[0]  # type: ignore
            )
    except googlemaps.exceptions.ApiError as e:
        # Covers any Google-side rejection — quota, auth, upstream 5xx — not only a bad key; the
        # log line preserves what Google actually answered.
        log.warning("Google geocode API call failed", exc_info=e)
        raise IncorrectGeocodeKeyError() from e
    except (IndexError, ValidationError) as e:
        context.emit_metric(
            MetricKey.ERROR.with_prefix("InvalidGoogleGeocodeResponse"), include_handler_properties=False
        )
        raise IncorrectCoordinatesError() from e

    return geocode_result.geometry.location
