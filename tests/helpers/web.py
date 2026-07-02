from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricsClient, NullBackend
from mitup_bot.web import create_app


def build_ptb_app_mock() -> MagicMock:
    """Build a MagicMock standing in for telegram.ext.Application.

    Lifecycle methods and `update_queue.put` (used by the webhook endpoint to
    enqueue parsed updates) are configured as AsyncMock so they can be awaited
    and inspected. The mock's `bot` attribute also exposes async stubs for the
    Telegram API calls performed during lifespan setup (currently only
    `set_webhook`).
    """
    ptb_app = MagicMock()
    ptb_app.initialize = AsyncMock()
    ptb_app.start = AsyncMock()
    ptb_app.stop = AsyncMock()
    ptb_app.shutdown = AsyncMock()
    ptb_app.update_queue.put = AsyncMock()

    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    ptb_app.bot = bot

    updater = MagicMock()
    updater.start_polling = AsyncMock()
    updater.stop = AsyncMock()
    ptb_app.updater = updater

    return ptb_app


def build_test_web_app(
    *,
    ptb_app: MagicMock | None = None,
    secret_token: str | None = "test-secret",
    metrics_client: MetricsClient | None = None,
    run_mode: RunModes = RunModes.WEBHOOK,
    webhook_url: str | None = "https://example.com/telegram",
    max_connections: int | None = 100,
) -> FastAPI:
    """Build a FastAPI app via the real factory with safe defaults.

    Tests that don't care about a specific argument can rely on the defaults;
    tests that exercise lifespan or argument forwarding should pass their own
    mocks so they can introspect them.
    """
    if ptb_app is None:
        ptb_app = build_ptb_app_mock()
    if metrics_client is None:
        metrics_client = MetricsClient(NullBackend())

    return create_app(
        ptb_app,
        secret_token=secret_token,
        metrics_client=metrics_client,
        run_mode=run_mode,
        webhook_url=webhook_url,
        max_connections=max_connections,
    )


def build_web_client(web_app: FastAPI) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient wired to the given FastAPI app via ASGITransport."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=web_app), base_url="http://test")


@asynccontextmanager
async def lifespan_runner(web_app: FastAPI) -> AsyncIterator[LifespanManager]:
    """Async context manager wrapping asgi_lifespan.LifespanManager."""
    async with LifespanManager(web_app) as manager:
        yield manager
