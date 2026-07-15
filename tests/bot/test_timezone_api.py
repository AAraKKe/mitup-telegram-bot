import asyncio
from unittest import mock

import googlemaps
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

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
from mitup_bot.monitoring import MetricKey, MetricUnit
from tests.helpers import AnyFloat
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.types import StubMitupContext

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
    timezone_api.__geocode_client = None
    timezone_api.__timezone_client = None


def assert_time_metrics_emitted(context: StubMitupContext, metrics: MetricAssertions, *metric_names: str):
    asyncio.run(context.metrics.flush())

    for name in metric_names:
        metrics.assert_emitted(name=name, value=AnyFloat(), unit=MetricUnit.MILLISECONDS)


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
    context: StubMitupContext,
    metrics: MetricAssertions,
):
    geocode_client.geocode.return_value = COORDIDATES_FROM_LOCATION
    timezone_client.timezone.return_value = {"timeZoneId": "America/New_York"}

    timezone = timezone_api.get_timezone_by_address("New York", context)

    geocode_client.geocode.assert_called_once_with("New York")
    timezone_client.timezone.assert_called_once_with(
        (
            COORDIDATES_FROM_LOCATION[0]["geometry"]["location"]["lat"],
            COORDIDATES_FROM_LOCATION[0]["geometry"]["location"]["lng"],
        )
    )
    assert timezone == "America/New_York"
    assert not caplog.text

    assert_time_metrics_emitted(context, metrics, "GoogleGeocodeApiTime", "GoogleTimeZoneApiTime")


def test_get_timezone_by_address_raises_with_missing_geocode_client(context: StubMitupContext):
    with pytest.raises(GeocodeClientNotConfiguredError):
        timezone_api.get_timezone_by_address("New York", context)


def test_get_timezone_by_address_raises_with_missing_timezone_client(
    geocode_client, context: StubMitupContext, metrics: MetricAssertions
):
    geocode_client.geocode.return_value = COORDIDATES_FROM_LOCATION

    with pytest.raises(TimezoneClientNotConfiguredError):
        timezone_api.get_timezone_by_address("New York", context)

    geocode_client.geocode.assert_called_once_with("New York")

    assert_time_metrics_emitted(context, metrics, "GoogleGeocodeApiTime")


def test_get_timezone_by_address_handles_geocode_failure(
    timezone_client, geocode_client, context: StubMitupContext, metrics: MetricAssertions
):
    geocode_client.geocode.return_value = []

    with pytest.raises(IncorrectCoordinatesError):
        timezone_api.get_timezone_by_address("New York", context)

    asyncio.run(context.metrics.flush())
    metrics.assert_emitted(
        name=MetricKey.ERROR.with_prefix("InvalidGoogleGeocodeResponse"), value=1, unit=MetricUnit.COUNT
    )


def test_get_timezone_by_location_success(timezone_client, context: StubMitupContext, metrics: MetricAssertions):
    timezone_client.timezone.return_value = {"timeZoneId": "America/Los_Angeles"}

    timezone = timezone_api.get_timezone_by_location(34.0522, -118.2437, context)

    assert timezone == "America/Los_Angeles"

    assert_time_metrics_emitted(context, metrics, "GoogleTimeZoneApiTime")


def test_get_timezone_by_location_raises_with_missing_timezone_client(context: StubMitupContext):
    with pytest.raises(TimezoneClientNotConfiguredError):
        timezone_api.get_timezone_by_location(34.0522, -118.2437, context)


def test_get_timezone_by_location_raise_incorrect_timezone_key_error(timezone_client, context: StubMitupContext):
    error = googlemaps.exceptions.ApiError(404)
    timezone_client.timezone.side_effect = error

    with capture_logs() as logs:
        with pytest.raises(IncorrectTimezoneKeyError):
            timezone_api.get_timezone_by_location(34.0522, -118.2437, context)

    # The Google response detail is preserved in a log line — the raised error alone reads as a
    # key problem even when the cause is quota/auth/upstream.
    warnings = [entry for entry in logs if entry["event"] == "Google timezone API call failed"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["exc_info"] is error


def test_get_coordinates_raise_incorrect_geocode_key_error(geocode_client, context: StubMitupContext):
    error = googlemaps.exceptions.ApiError(404)
    geocode_client.geocode.side_effect = error

    with capture_logs() as logs:
        with pytest.raises(IncorrectGeocodeKeyError):
            timezone_api.get_coordinates("New York", context)

    warnings = [entry for entry in logs if entry["event"] == "Google geocode API call failed"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["exc_info"] is error


def test_get_timezone_by_location_raises_incorrect_coordinates_error(timezone_client, context: StubMitupContext):
    timezone_client.timezone.return_value = None

    with pytest.raises(IncorrectCoordinatesError):
        timezone_api.get_timezone_by_location(34.0522, -118.2437, context)


def test_get_coordinates_raises_with_missing_geocode_client(context: StubMitupContext):
    with pytest.raises(GeocodeClientNotConfiguredError):
        timezone_api.get_coordinates("New York", context)


def test_get_coordinates_logs_warning_on_failure(geocode_client, context: StubMitupContext, metrics: MetricAssertions):
    geocode_client.geocode.return_value = []

    with pytest.raises(IncorrectCoordinatesError):
        timezone_api.get_coordinates("Invalid address", context)

    asyncio.run(context.metrics.flush())
    metrics.assert_emitted(
        name=MetricKey.ERROR.with_prefix("InvalidGoogleGeocodeResponse"), value=1, unit=MetricUnit.COUNT
    )


# ---------------------------------------------------------------------------
# Direct accessor functions raise when not configured
# ---------------------------------------------------------------------------


def test_geocode_client_raises_when_not_configured():
    """geocode_client() raises GeocodeClientNotConfiguredError when configure() was never called."""
    # reset_clients autouse fixture guarantees __geocode_client is None here.
    with pytest.raises(GeocodeClientNotConfiguredError):
        timezone_api.geocode_client()


def test_geocode_client_returns_client_when_configured(gmaps_client):
    """geocode_client() returns the configured client (exercises the return path at line 40)."""
    timezone_api.configure(CONFIG_GMAPS_KEYS)

    client = timezone_api.geocode_client()

    assert client is not None


def test_timezone_client_raises_when_not_configured():
    """timezone_client() raises TimezoneClientNotConfiguredError when configure() was never called."""
    # reset_clients autouse fixture guarantees __timezone_client is None here.
    with pytest.raises(TimezoneClientNotConfiguredError):
        timezone_api.timezone_client()


def test_timezone_client_returns_client_when_configured(gmaps_client):
    """timezone_client() returns the configured client (exercises the return path at line 46)."""
    timezone_api.configure(CONFIG_GMAPS_KEYS)

    client = timezone_api.timezone_client()

    assert client is not None


def test_configure_raises_timezone_already_initialized_when_only_timezone_client_set(gmaps_client):
    """configure() raises TimezoneClientAlreadyInitializedError when __timezone_client is already
    set but __geocode_client is still None.

    This exercises the branch at line 62 of timezone_api.py which is only reachable when
    __geocode_client is falsy and __timezone_client is truthy.
    """
    # Inject a pre-existing timezone client while leaving geocode client as None.
    timezone_api.__timezone_client = mock.MagicMock()

    with pytest.raises(TimezoneClientAlreadyInitializedError):
        timezone_api.configure(CONFIG_GMAPS_KEYS)
