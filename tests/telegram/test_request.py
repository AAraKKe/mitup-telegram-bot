from unittest import mock

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs
from telegram.error import TimedOut
from telegram.request import HTTPXRequest, RequestData

from mitup_bot.config import BotConfig
from mitup_bot.monitoring import MetricsClient, MetricUnit, bound_metrics_client
from mitup_bot.request import InstrumentedHTTPXRequest, api_method_from_url, build_telegram_request, request_ids
from tests.helpers import AnyFloat
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

# A token-shaped value, so an assertion that it never appears is a real proof rather than a
# coincidence of the fixture's shape.
BOT_TOKEN = "7654321:AAHfake-Token-Value-For-Tests-0123456789"
SEND_MESSAGE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


@pytest.fixture
def client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(client)


def stub_request_data(**parameters: object) -> RequestData:
    return mock.MagicMock(spec=RequestData, parameters=parameters)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SEND_MESSAGE_URL, "sendMessage"),
        (f"https://api.telegram.org/file/bot{BOT_TOKEN}/documents/file_7.csv", "downloadFile"),
        (f"https://api.telegram.org/bot{BOT_TOKEN}", "unknown"),
    ],
    ids=["method", "file_download", "unrecognised_shape"],
)
def test_the_api_method_is_read_from_the_segment_after_the_token_prefix(url: str, expected: str):
    assert api_method_from_url(url) == expected


def test_only_the_id_parameters_of_a_request_may_be_recorded():
    ids = request_ids(stub_request_data(chat_id=42, message_id=7, text="a private message"))

    assert ids == {"chat_id": 42, "message_id": 7}


async def test_a_round_trip_records_its_method_and_never_the_token(client: MetricsClient, metrics: MetricAssertions):
    request = InstrumentedHTTPXRequest()

    with capture_logs() as logs:
        with mock.patch.object(HTTPXRequest, "do_request", return_value=(200, b"{}")):
            with bound_metrics_client(client):
                await request.do_request(SEND_MESSAGE_URL, "POST", stub_request_data(chat_id=42))

    await client.flush()

    (line,) = [entry for entry in logs if entry["event"] == "Telegram API call"]
    assert line["api_method"] == "sendMessage"
    assert line["status_code"] == 200
    assert line["chat_id"] == 42

    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name="TelegramApiFault", value=0, times=1)

    # The one rule this module exists to keep: the Bot API URL carries the token, so no part of it
    # — and nothing else the token could hide in — may reach either plane.
    emitted = repr(logs) + repr([(record.name, record.properties) for record in client.records])
    assert BOT_TOKEN not in emitted
    assert "api.telegram.org" not in emitted


async def test_a_stalled_round_trip_is_recorded_as_a_timeout(client: MetricsClient, metrics: MetricAssertions):
    request = InstrumentedHTTPXRequest()

    with capture_logs() as logs:
        with mock.patch.object(HTTPXRequest, "do_request", side_effect=TimedOut()):
            with bound_metrics_client(client), pytest.raises(TimedOut):
                await request.do_request(SEND_MESSAGE_URL, "POST")

    await client.flush()

    (line,) = [entry for entry in logs if entry["event"] == "Telegram API call"]
    assert line["outcome"] == "timeout"
    assert line["error_type"] == "telegram.error.TimedOut"
    metrics.assert_emitted(name="TelegramApiFault", value=1, times=1)
    assert BOT_TOKEN not in repr(logs)


async def test_the_per_call_line_can_be_switched_off_by_config(client: MetricsClient, metrics: MetricAssertions):
    request = build_telegram_request(BotConfig(token=SecretStr("test-token"), api_call_log_enabled=False))

    with capture_logs() as logs:
        with mock.patch.object(HTTPXRequest, "do_request", return_value=(200, b"{}")):
            with bound_metrics_client(client):
                await request.do_request(SEND_MESSAGE_URL, "POST")

    await client.flush()

    assert logs == []
    metrics.assert_emitted(name="TelegramApiTime", value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
