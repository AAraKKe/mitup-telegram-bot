import asyncio
import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs
from telegram import Update

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.web.telegram import TELEGRAM_SECRET_HEADER
from tests.helpers import MetricAssertions, build_ptb_app_mock, build_test_web_app, build_web_client
from tests.helpers.monitoring import FlushCountingBackend

SECRET = "test-secret"
VALID_UPDATE_PAYLOAD = {"update_id": 1}


@pytest.fixture
def ptb_app() -> MagicMock:
    return build_ptb_app_mock()


@pytest.fixture
def web_app(ptb_app: MagicMock, metrics_client: MetricsClient) -> FastAPI:
    return build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
    )


async def test_valid_secret_and_payload_returns_204_and_enqueues_update(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: SECRET},
        )

    assert response.status_code == 204
    ptb_app.update_queue.put.assert_awaited_once()
    forwarded = ptb_app.update_queue.put.await_args.args[0]
    assert isinstance(forwarded, Update)
    assert forwarded.update_id == 1
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_FORBIDDEN)
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE)


async def test_valid_update_returns_204_without_processing_running(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    # Swap the queue stub for a real asyncio.Queue that nothing drains. If the
    # response still comes back 204 while the update sits unconsumed in the queue,
    # the endpoint provably does not await handler processing before responding —
    # updates are handed off to PTB's out-of-band fetcher, not processed in-request.
    queue: asyncio.Queue[Update] = asyncio.Queue()
    ptb_app.update_queue = queue

    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: SECRET},
        )

    assert response.status_code == 204
    # The update was enqueued but never consumed — the 204 did not wait on it.
    assert queue.qsize() == 1
    enqueued = queue.get_nowait()
    assert isinstance(enqueued, Update)
    assert enqueued.update_id == 1
    # No direct, in-request processing path (the removed process_update call).
    ptb_app.process_update.assert_not_called()
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_FORBIDDEN)
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE)


async def test_enqueue_failure_still_returns_204_and_logs(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions, caplog: pytest.LogCaptureFixture
):
    # The endpoint guards update_queue.put: if enqueuing raises (broken event loop,
    # bad app state), it must still return 204 so Telegram doesn't retry the update
    # in a tight loop. The failure is logged, not surfaced as an error metric.
    ptb_app.update_queue.put.side_effect = RuntimeError("event loop is broken")

    with caplog.at_level(logging.ERROR, logger="mitup_bot.web.telegram"):
        async with build_web_client(web_app) as client:
            response = await client.post(
                "/telegram",
                json=VALID_UPDATE_PAYLOAD,
                headers={TELEGRAM_SECRET_HEADER: SECRET},
            )

    assert response.status_code == 204
    ptb_app.update_queue.put.assert_awaited_once()
    assert "Failed to enqueue Telegram update" in caplog.text
    # An enqueue failure is neither a forbidden nor a malformed-payload condition.
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_FORBIDDEN)
    metrics.assert_not_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE)


async def test_missing_secret_header_returns_403_and_emits_forbidden_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    async with build_web_client(web_app) as client:
        response = await client.post("/telegram", json=VALID_UPDATE_PAYLOAD)

    assert response.status_code == 403
    ptb_app.update_queue.put.assert_not_called()
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
    ptb_app.update_queue.put.assert_not_called()
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
    ptb_app.update_queue.put.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_FORBIDDEN, value=1)


async def test_non_ascii_secret_header_returns_403_and_emits_forbidden_metric(
    web_app: FastAPI, ptb_app: MagicMock, metrics: MetricAssertions
):
    # The ASGI layer decodes header values as latin-1, so a byte >= 0x80 reaches the endpoint
    # as a non-ASCII str — the one input a str-level compare_digest cannot handle (TypeError).
    # Any unauthenticated caller can send it, so it must land on the same 403 plus rejection
    # metric as any other wrong secret, never on a 500 that skips the observability entirely.
    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers=[(TELEGRAM_SECRET_HEADER.encode(), b"test-secre\xff")],
        )

    assert response.status_code == 403
    ptb_app.update_queue.put.assert_not_called()
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
    ptb_app.update_queue.put.assert_not_called()
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
    ptb_app.update_queue.put.assert_not_called()
    metrics.assert_emitted(name=MetricKey.WEBHOOK_MALFORMED_UPDATE, value=1)


async def test_request_flushes_the_web_metrics_client(ptb_app: MagicMock):
    backend = FlushCountingBackend()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=MetricsClient(backend),
        run_mode=RunModes.WEBHOOK,
    )

    async with build_web_client(web_app) as client:
        response = await client.post(
            "/telegram",
            json=VALID_UPDATE_PAYLOAD,
            headers={TELEGRAM_SECRET_HEADER: SECRET},
        )

    assert response.status_code == 204
    assert backend.flush_count == 1


async def test_rejected_request_still_flushes_the_web_metrics_client(ptb_app: MagicMock):
    backend = FlushCountingBackend()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=MetricsClient(backend),
        run_mode=RunModes.WEBHOOK,
    )

    async with build_web_client(web_app) as client:
        response = await client.post("/telegram", json=VALID_UPDATE_PAYLOAD)

    assert response.status_code == 403
    assert backend.flush_count == 1


async def test_a_received_update_is_logged_with_its_queue_depth(web_app: FastAPI, ptb_app: MagicMock):
    """Thousands of successful arrivals a day write nothing today: no arrival timestamp to
    difference against processing, and no number for a backlog."""
    queue: asyncio.Queue[Update] = asyncio.Queue()
    ptb_app.update_queue = queue

    with capture_logs() as logs:
        async with build_web_client(web_app) as client:
            response = await client.post(
                "/telegram",
                json={"update_id": 1, "message": {"message_id": 2, "date": 0, "chat": {"id": 3, "type": "private"}}},
                headers={TELEGRAM_SECRET_HEADER: SECRET},
            )

    assert response.status_code == 204
    arrivals = [entry for entry in logs if entry["event"] == "Telegram update received"]
    assert len(arrivals) == 1
    assert arrivals[0]["log_level"] == "info"
    assert arrivals[0]["update_id"] == 1
    assert arrivals[0]["update_type"] == "message"
    assert arrivals[0]["chat_id"] == 3
    assert arrivals[0]["chat_type"] == "private"
    assert arrivals[0]["transport"] == "webhook"
    # Depth before the hand-off: nothing drains the queue in this test.
    assert arrivals[0]["queue_depth"] == 0


async def test_a_rejected_update_never_reaches_the_arrival_line(web_app: FastAPI, ptb_app: MagicMock):
    """The line stands for an update that entered the system; a 403 is not one."""
    with capture_logs() as logs:
        async with build_web_client(web_app) as client:
            response = await client.post("/telegram", json=VALID_UPDATE_PAYLOAD)

    assert response.status_code == 403
    assert [entry for entry in logs if entry["event"] == "Telegram update received"] == []
