import asyncio
from enum import Enum, StrEnum, auto

import googlemaps
import structlog
from googlemaps import Client
from pydantic import BaseModel, ValidationError

from mitup_bot.config import GoogleApiConfig
from mitup_bot.exceptions import (
    GeocodeClientAlreadyInitializedError,
    GeocodeClientNotConfiguredError,
    IncorrectGeocodeKeyError,
    IncorrectKeyError,
    IncorrectTimezoneKeyError,
    TimezoneClientAlreadyInitializedError,
    TimezoneClientNotConfiguredError,
)
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.monitoring.outbound import GOOGLE_EDGE, outbound_call

log = structlog.get_logger(__name__)

TIMEZONE_API_METHOD = "timezone"
GEOCODE_API_METHOD = "geocode"
GOOGLE_TIMEOUT_ERRORS = (googlemaps.exceptions.Timeout,)

__geocode_client: googlemaps.Client | None = None
__timezone_client: googlemaps.Client | None = None


class GeocodingLocation(BaseModel):
    lat: float
    lng: float


class GeocodingGeometry(BaseModel):
    location: GeocodingLocation


class GeocodingResponse(BaseModel):
    geometry: GeocodingGeometry


class TimezoneLookupFailure(Enum):
    """Why a lookup produced no timezone. Callers bind it as `reason` on the line they log.

    The reason has to survive the return: an unresolvable address is an ordinary typo the user can
    correct, while an unreadable Google response is ours to investigate, and a bare `None` reads
    the same for both. Deliberately not a `StrEnum`, so that a member stays distinguishable from a
    timezone id in the `str | TimezoneLookupFailure` returns below.
    """

    ADDRESS_NOT_GEOCODED = "address_not_geocoded"
    GEOCODE_RESPONSE_UNUSABLE = "geocode_response_unusable"
    COORDINATES_WITHOUT_TIMEZONE = "coordinates_without_timezone"


class TimezoneInputMethod(StrEnum):
    """How the user supplied the place to resolve; also the `InputMethod` EMF property value."""

    MESSAGE = auto()
    LOCATION = auto()


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


async def get_timezone_by_address(address: str, context: TMitupContext) -> str | TimezoneLookupFailure:
    """Resolve an address to a timezone ID, or name why it resolved to none.

    A failure means the user's input is unusable and they should be asked to try again; a
    Google-side failure (quota, auth, upstream) raises instead, because that is an operator problem.
    """

    location = await get_coordinates(address, context)
    if isinstance(location, TimezoneLookupFailure):
        return location

    return await get_timezone_by_location(location.lat, location.lng, context)


async def get_timezone_by_location(
    latitude: float, longitude: float, context: TMitupContext
) -> str | TimezoneLookupFailure:
    """Resolve a coordinate pair to a timezone ID, or name why Google mapped it to no zone."""

    client = timezone_client()
    try:
        with outbound_call(
            GOOGLE_EDGE,
            TIMEZONE_API_METHOD,
            timeout_errors=GOOGLE_TIMEOUT_ERRORS,
            client=context.metrics,
            latitude=round(latitude, 2),
            longitude=round(longitude, 2),
        ):
            # The Google client is requests-based and blocking, so the call runs off the event
            # loop: on the default single-update cap it would otherwise stall every other update
            # for two round-trips to Google while someone types an address.
            timezone = await asyncio.to_thread(client.timezone, (latitude, longitude))  # type: ignore
    except googlemaps.exceptions.ApiError as e:
        # Covers any Google-side rejection — quota, auth, upstream 5xx — not only a bad key; the
        # log line preserves what Google actually answered.
        log.warning("Google timezone API call failed", exc_info=e)
        raise IncorrectTimezoneKeyError() from e

    if timezone is None:
        context.put_feature_metric(
            Feature.SET_TIMEZONE, name=MetricKey.ERROR, properties={"reason": "invalid_google_timezone_response"}
        )
        return TimezoneLookupFailure.COORDINATES_WITHOUT_TIMEZONE

    return timezone["timeZoneId"]


async def get_coordinates(address: str, context: TMitupContext) -> GeocodingLocation | TimezoneLookupFailure:
    """Return the coordinates of an address, or the reason Google's answer yielded none."""

    client = geocode_client()
    try:
        with outbound_call(
            GOOGLE_EDGE,
            GEOCODE_API_METHOD,
            timeout_errors=GOOGLE_TIMEOUT_ERRORS,
            client=context.metrics,
            address_len=len(address),
        ):
            # Blocking client, moved off the event loop for the same reason as the timezone call.
            results = await asyncio.to_thread(client.geocode, address)  # type: ignore
    except googlemaps.exceptions.ApiError as e:
        # Covers any Google-side rejection — quota, auth, upstream 5xx — not only a bad key; the
        # log line preserves what Google actually answered.
        log.warning("Google geocode API call failed", exc_info=e)
        raise IncorrectGeocodeKeyError() from e

    try:
        geocode_result = GeocodingResponse.model_validate(results[0])
    except (IndexError, ValidationError) as error:
        context.put_feature_metric(
            Feature.SET_TIMEZONE, name=MetricKey.ERROR, properties={"reason": "invalid_google_geocode_response"}
        )
        # An empty result list is Google saying it knows no such place; a result the model rejects
        # is Google answering in a shape we cannot read. Only the first is the user's to fix.
        if isinstance(error, IndexError):
            return TimezoneLookupFailure.ADDRESS_NOT_GEOCODED
        return TimezoneLookupFailure.GEOCODE_RESPONSE_UNUSABLE

    return geocode_result.geometry.location
