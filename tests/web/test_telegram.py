import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from telegram import Update

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricKey, MetricsClient, NullBackend
from mitup_bot.web.telegram import TELEGRAM_SECRET_HEADER
from tests.helpers import MetricAssertions, build_ptb_app_mock, build_test_web_app, build_web_client

SECRET = "test-secret"
VALID_UPDATE_PAYLOAD = {"update_id": 1}


@pytest.fixture
def ptb_app() -> MagicMock:
    return build_ptb_app_mock()


@pytest.fixture
def metrics_client() -> MetricsClient:
    return MetricsClient(NullBackend())


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


@pytest.fixture
def web_app(ptb_app: MagicMock, metrics_client: MetricsClient) -> FastAPI:
    return build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
    )


async def test_valid_secret_and_payload_returns_204_and_processes_update(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: SECRET},
        )

    assert response.status_code == 204
    ptb_app.process_update.assert_awaited_once()
    forwarded = ptb_app.process_update.await_args.args[0]
    assert isinstance(forwarded, Update)
    assert forwarded.update_id == 1
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_FORBIDDEN)
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE)


async def test_missing_secret_header_returns_403_and_emits_forbidden_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post("/telegram", json=VALID_UPDATE_PAYLOAD)

    assert response.status_code == 403
    ptb_app.process_update.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_FORBIDDEN, value=1)


async def test_wrong_secret_same_length_returns_403_and_emits_forbidden_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    # Same length as SECRET ("test-secret" = 11 chars), one byte different
    wrong_secret = "test-secreX"
    assert len(wrong_secret) == len(SECRET)  # sanity: matches the brief

    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: wrong_secret},
        )

    assert response.status_code == 403
    ptb_app.process_update.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_FORBIDDEN, value=1)


async def test_wrong_secret_different_length_returns_403_and_emits_forbidden_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: "wrong"},
        )

    assert response.status_code == 403
    ptb_app.process_update.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_FORBIDDEN, value=1)


async def test_malformed_json_body_returns_204_and_emits_malformed_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            content=b"not-json{",
            headers={
                TELEGRAM_SECRET_HEADER: SECRET,
                "Content-Type": "application/json",
            },
        )

    # Telegram retries on non-2xx — always ack to drop poison pills.
    assert response.status_code == 204
    ptb_app.process_update.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE, value=1)


@pytest.mark.parametrize(
    "bad_payload",
    [
        # Missing required update_id field — Update.__init__ raises TypeError
        {"foo": "bar"},
        # Empty object — also missing update_id
        {},
    ],
    ids=["missing_update_id", "empty_object"],
)
async def test_valid_json_but_invalid_update_returns_204_and_emits_malformed_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions, bad_payload: dict
):
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=bad_payload,
            headers={TELEGRAM_SECRET_HEADER: SECRET},
        )

    # Telegram retries on non-2xx — ack even when Update.de_json rejects the payload.
    assert response.status_code == 204
    ptb_app.process_update.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE, value=1)


async def test_process_update_exception_is_logged_and_returns_204(
    web_app: FastAPI, ptb_app: MagicMock, caplog: pytest.LogCaptureFixture
):
    ptb_app.process_update = AsyncMock(side_effect=RuntimeError("handler boom"))

    with caplog.at_level(logging.ERROR, logger="mitup_bot.web.telegram"):
        async with build_web_client(web_app) as client:
            response = await client.post(
                "/telegram",
                json=VALID_UPDATE_PAYLOAD,
                headers={TELEGRAM_SECRET_HEADER: SECRET},
            )

    # Returning 2xx prevents Telegram from retrying a buggy handler in a tight loop.
    assert response.status_code == 204
    ptb_app.process_update.assert_awaited_once()
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected an ERROR log when process_update raises"
