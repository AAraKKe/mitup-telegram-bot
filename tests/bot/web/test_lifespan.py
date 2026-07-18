from unittest.mock import AsyncMock, MagicMock

import pytest

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.helpers import MetricAssertions, build_test_web_app, lifespan_runner
from tests.helpers.monitoring import FlushCountingBackend

WEBHOOK_URL = "https://example.com/telegram"
SECRET = "test-secret"
MAX_CONNECTIONS = 100


def build_tracked_ptb_app() -> tuple[MagicMock, MagicMock]:
    """Build a ptb_app mock whose lifecycle methods share a single parent.

    The parent MagicMock captures interleaved calls across `ptb_app`,
    `ptb_app.bot`, and `ptb_app.updater`, so tests can assert ordering via
    `parent.mock_calls`.
    """
    parent = MagicMock()

    ptb_app = MagicMock()
    ptb_app.initialize = AsyncMock()
    ptb_app.start = AsyncMock()
    ptb_app.stop = AsyncMock()
    ptb_app.shutdown = AsyncMock()
    ptb_app.process_update = AsyncMock()

    bot = MagicMock()
    bot.set_webhook = AsyncMock()
    ptb_app.bot = bot

    updater = MagicMock()
    updater.start_polling = AsyncMock()
    updater.stop = AsyncMock()
    ptb_app.updater = updater

    # Attach each async method to the parent so call order is recorded globally.
    parent.attach_mock(ptb_app.initialize, "initialize")
    parent.attach_mock(ptb_app.start, "start")
    parent.attach_mock(ptb_app.stop, "stop")
    parent.attach_mock(ptb_app.shutdown, "shutdown")
    parent.attach_mock(bot.set_webhook, "set_webhook")
    parent.attach_mock(updater.start_polling, "updater_start_polling")
    parent.attach_mock(updater.stop, "updater_stop")

    return ptb_app, parent


def call_names(parent: MagicMock) -> list[str]:
    """Return the ordered list of method names invoked on the parent mock."""
    return [name for name, _args, _kwargs in parent.mock_calls if "." not in name]


# --- Webhook lifespan ---


async def test_webhook_lifespan_startup_order_initialize_start_set_webhook(metrics_client: MetricsClient):
    ptb_app, parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    async with lifespan_runner(web_app):
        startup_calls = call_names(parent)

    assert startup_calls[:3] == ["initialize", "start", "set_webhook"]


async def test_webhook_lifespan_set_webhook_args(metrics_client: MetricsClient):
    ptb_app, _parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    async with lifespan_runner(web_app):
        ...

    ptb_app.bot.set_webhook.assert_awaited_once()
    kwargs = ptb_app.bot.set_webhook.await_args.kwargs
    assert kwargs["url"] == WEBHOOK_URL
    assert kwargs["secret_token"] == SECRET
    assert kwargs["max_connections"] == MAX_CONNECTIONS
    # allowed_updates must NOT be passed — omitting it preserves Telegram's previous
    # subscription, whose default set already includes my_chat_member (needed to detect
    # users blocking the bot). Passing an explicit list could silently drop update types
    # the bot relies on, such as inline queries.
    assert "allowed_updates" not in kwargs


async def test_webhook_lifespan_shutdown_order_stop_shutdown(metrics_client: MetricsClient):
    ptb_app, parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    async with lifespan_runner(web_app):
        ...

    calls = call_names(parent)
    # Shutdown happens after the startup trio: index 3 onwards.
    assert calls[3:] == ["stop", "shutdown"]


async def test_webhook_lifespan_registers_patreon_webhook_after_set_webhook(
    metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    from mitup_bot.web import app as web_app_module

    register_mock = AsyncMock()
    monkeypatch.setattr(web_app_module.patreon_webhooks, "register_membership_webhook", register_mock)

    ptb_app, _parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
        patreon_webhook_url="https://example.com:443/patreon/webhook?include=user",
    )

    async with lifespan_runner(web_app):
        ...

    register_mock.assert_awaited_once_with("https://example.com:443/patreon/webhook?include=user", metrics_client)


async def test_webhook_lifespan_skips_patreon_registration_when_url_absent(
    metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    from mitup_bot.web import app as web_app_module

    register_mock = AsyncMock()
    monkeypatch.setattr(web_app_module.patreon_webhooks, "register_membership_webhook", register_mock)

    ptb_app, _parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
        patreon_webhook_url=None,
    )

    async with lifespan_runner(web_app):
        ...

    register_mock.assert_not_awaited()


# --- Polling lifespan ---


async def test_polling_lifespan_startup_order_initialize_start_polling_start(metrics_client: MetricsClient):
    ptb_app, parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=None,
        metrics_client=metrics_client,
        run_mode=RunModes.POLLING,
        webhook_url=None,
        max_connections=None,
    )

    async with lifespan_runner(web_app):
        startup_calls = call_names(parent)

    assert startup_calls[:3] == ["initialize", "updater_start_polling", "start"]


async def test_polling_lifespan_does_not_call_set_webhook(metrics_client: MetricsClient):
    ptb_app, _parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=None,
        metrics_client=metrics_client,
        run_mode=RunModes.POLLING,
        webhook_url=None,
        max_connections=None,
    )

    async with lifespan_runner(web_app):
        ...

    ptb_app.bot.set_webhook.assert_not_called()


async def test_polling_lifespan_shutdown_order_updater_stop_stop_shutdown(metrics_client: MetricsClient):
    ptb_app, parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=None,
        metrics_client=metrics_client,
        run_mode=RunModes.POLLING,
        webhook_url=None,
        max_connections=None,
    )

    async with lifespan_runner(web_app):
        ...

    calls = call_names(parent)
    assert calls[3:] == ["updater_stop", "stop", "shutdown"]


# --- Failures ---


async def test_startup_failure_when_initialize_raises_emits_metric_and_propagates(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, _parent = build_tracked_ptb_app()
    ptb_app.initialize = AsyncMock(side_effect=RuntimeError("init boom"))
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    with pytest.raises(RuntimeError, match="init boom"):
        async with lifespan_runner(web_app):
            pytest.fail("Lifespan startup should have raised")

    metrics.assert_emitted(name=MetricKey.LIFESPAN_STARTUP_FAILED, value=1)


async def test_startup_failure_when_set_webhook_raises_emits_metric_and_propagates(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, _parent = build_tracked_ptb_app()
    ptb_app.bot.set_webhook = AsyncMock(side_effect=RuntimeError("set_webhook boom"))
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    with pytest.raises(RuntimeError, match="set_webhook boom"):
        async with lifespan_runner(web_app):
            pytest.fail("Lifespan startup should have raised")

    metrics.assert_emitted(name=MetricKey.LIFESPAN_STARTUP_FAILED, value=1)


async def test_shutdown_failure_when_stop_raises_emits_metric_and_continues(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, parent = build_tracked_ptb_app()
    ptb_app.stop = AsyncMock(side_effect=RuntimeError("stop boom"))
    parent.attach_mock(ptb_app.stop, "stop")
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    async with lifespan_runner(web_app):
        ...

    metrics.assert_emitted(name=MetricKey.LIFESPAN_SHUTDOWN_FAILED, value=1)
    # Subsequent steps must still run even though stop raised.
    ptb_app.shutdown.assert_awaited_once()


async def test_polling_shutdown_failure_when_updater_stop_raises_emits_metric_and_continues(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, parent = build_tracked_ptb_app()
    ptb_app.updater.stop = AsyncMock(side_effect=RuntimeError("updater stop boom"))
    parent.attach_mock(ptb_app.updater.stop, "updater_stop")
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=None,
        metrics_client=metrics_client,
        run_mode=RunModes.POLLING,
        webhook_url=None,
        max_connections=None,
    )

    async with lifespan_runner(web_app):
        ...

    metrics.assert_emitted(name=MetricKey.LIFESPAN_SHUTDOWN_FAILED, value=1)
    # Subsequent steps must still run even though updater.stop raised.
    ptb_app.stop.assert_awaited_once()
    ptb_app.shutdown.assert_awaited_once()


@pytest.mark.parametrize(
    "run_mode",
    [RunModes.WEBHOOK, RunModes.POLLING],
    ids=["webhook", "polling"],
)
async def test_shutdown_failure_when_shutdown_raises_emits_metric(
    run_mode: RunModes, metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, parent = build_tracked_ptb_app()
    ptb_app.shutdown = AsyncMock(side_effect=RuntimeError("shutdown boom"))
    parent.attach_mock(ptb_app.shutdown, "shutdown")
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET if run_mode is RunModes.WEBHOOK else None,
        metrics_client=metrics_client,
        run_mode=run_mode,
        webhook_url=WEBHOOK_URL if run_mode is RunModes.WEBHOOK else None,
        max_connections=MAX_CONNECTIONS if run_mode is RunModes.WEBHOOK else None,
    )

    async with lifespan_runner(web_app):
        ...

    metrics.assert_emitted(name=MetricKey.LIFESPAN_SHUTDOWN_FAILED, value=1)


@pytest.mark.parametrize(
    "run_mode",
    [RunModes.WEBHOOK, RunModes.POLLING],
    ids=["webhook", "polling"],
)
async def test_lifespan_flushes_metrics_after_startup_and_shutdown(run_mode: RunModes):
    backend = FlushCountingBackend()
    ptb_app, _parent = build_tracked_ptb_app()
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET if run_mode is RunModes.WEBHOOK else None,
        metrics_client=MetricsClient(backend),
        run_mode=run_mode,
        webhook_url=WEBHOOK_URL if run_mode is RunModes.WEBHOOK else None,
        max_connections=MAX_CONNECTIONS if run_mode is RunModes.WEBHOOK else None,
    )

    async with lifespan_runner(web_app):
        assert backend.flush_count == 1

    assert backend.flush_count == 2


async def test_startup_failure_flushes_the_metric_before_propagating():
    backend = FlushCountingBackend()
    ptb_app, _parent = build_tracked_ptb_app()
    ptb_app.initialize = AsyncMock(side_effect=RuntimeError("init boom"))
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=SECRET,
        metrics_client=MetricsClient(backend),
        run_mode=RunModes.WEBHOOK,
        webhook_url=WEBHOOK_URL,
        max_connections=MAX_CONNECTIONS,
    )

    with pytest.raises(RuntimeError, match="init boom"):
        async with lifespan_runner(web_app):
            pytest.fail("Lifespan startup should have raised")

    assert backend.flush_count == 1


async def test_polling_startup_failure_emits_metric_flushes_and_propagates(
    metrics_client: MetricsClient, metrics: MetricAssertions
):
    ptb_app, _parent = build_tracked_ptb_app()
    ptb_app.updater.start_polling = AsyncMock(side_effect=RuntimeError("polling boom"))
    web_app = build_test_web_app(
        ptb_app=ptb_app,
        secret_token=None,
        metrics_client=metrics_client,
        run_mode=RunModes.POLLING,
        webhook_url=None,
        max_connections=None,
    )

    with pytest.raises(RuntimeError, match="polling boom"):
        async with lifespan_runner(web_app):
            pytest.fail("Lifespan startup should have raised")

    metrics.assert_emitted(name=MetricKey.LIFESPAN_STARTUP_FAILED, value=1)
