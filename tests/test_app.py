import contextlib
import dataclasses
import gc
import logging
from collections.abc import Generator
from unittest import mock

import pytest
import structlog
from pydantic import SecretStr
from structlog._config import BoundLoggerLazyProxy

from mitup_bot.app import MitupRuntime
from mitup_bot.config import (
    AppConfig,
    BotConfig,
    Config,
    DbConfig,
    Env,
    GoogleApiConfig,
    MetricsConfig,
    MetricsEnv,
    RunModes,
    TomlConfigProvider,
)
from mitup_bot.update_processor import PerUserUpdateProcessor


def _build_config(
    *,
    run_mode: RunModes = RunModes.POLLING,
    domain: str | None = None,
    secret_token: SecretStr | None = None,
) -> Config:
    return Config(
        db=DbConfig(
            username="user",
            password=SecretStr("password"),
            url="testhost",
            database="db",
        ),
        bot=BotConfig(
            token=SecretStr("fake-bot-token"),
            domain=domain,
            secret_token=secret_token,
        ),
        google_api=GoogleApiConfig(
            gmaps_geocode_key=SecretStr("geocode-key"),
            gmaps_timezone_key=SecretStr("timezone-key"),
        ),
        app=AppConfig(run_mode=run_mode),
        metrics=MetricsConfig(namespace="test", environment=MetricsEnv.STDOUT, flush_on_emission=False),
    )


@dataclasses.dataclass
class RuntimeDeps:
    config: mock.MagicMock
    builder: mock.MagicMock
    builder_instance: mock.MagicMock
    db: mock.MagicMock
    tz: mock.MagicMock
    metrics: mock.MagicMock
    registry: mock.MagicMock


@pytest.fixture
def _patch_runtime_deps(request: pytest.FixtureRequest) -> Generator[RuntimeDeps]:
    """Patch all external dependencies that MitupRuntime.__init__ calls.

    By default this also stubs out the production `configure_logging`. The deterministic test
    pipeline (installed by the autouse `deterministic_structlog` fixture) coerces non-primitive
    bound values to strings so captured records stay serializable under xdist — but the production
    `configure_logging` swaps in structlog's `ProcessorFormatter.wrap_for_formatter` pipeline, which
    attaches live, non-serializable objects (`_logger`, `_record`) onto every record AND bypasses
    that coercion. `MitupRuntime.__init__`/`run()` log while pytest is capturing, so letting the
    real call run would ship those objects across execnet and crash the worker. Stubbing it keeps
    the deterministic pipeline in force for runtime construction.

    Tests that intentionally assert on what `configure_logging` installs opt out by requesting the
    `_real_configure_logging` fixture.
    """
    stub_logging = "_real_configure_logging" not in request.fixturenames
    with (
        mock.patch("mitup_bot.app.Config.from_providers", return_value=_build_config()) as mock_config,
        mock.patch("mitup_bot.app.Application.builder") as mock_builder,
        mock.patch("mitup_bot.app.db.configure_db") as mock_db,
        mock.patch("mitup_bot.app.timezone_api.configure") as mock_tz,
        mock.patch("mitup_bot.app.configure_emf_backend") as mock_metrics,
        mock.patch("mitup_bot.app.HandlersRegistry") as mock_registry,
        mock.patch("mitup_bot.app.configure_logging") if stub_logging else contextlib.nullcontext(),
    ):
        builder_instance = mock.MagicMock()
        mock_builder.return_value = builder_instance
        builder_instance.token.return_value = builder_instance
        builder_instance.defaults.return_value = builder_instance
        builder_instance.context_types.return_value = builder_instance
        builder_instance.rate_limiter.return_value = builder_instance
        builder_instance.build.return_value = mock.MagicMock()

        yield RuntimeDeps(
            config=mock_config,
            builder=mock_builder,
            builder_instance=builder_instance,
            db=mock_db,
            tz=mock_tz,
            metrics=mock_metrics,
            registry=mock_registry,
        )


@pytest.fixture
def runtime(_patch_runtime_deps: RuntimeDeps) -> MitupRuntime:
    return MitupRuntime(Env.DEV)


# --- Init ---


def test_init_sets_env(_patch_runtime_deps: RuntimeDeps):
    runtime = MitupRuntime(Env.DEV)

    assert runtime.env is Env.DEV


def test_init_sets_registry_env(_patch_runtime_deps: RuntimeDeps):
    MitupRuntime(Env.PROD)

    assert _patch_runtime_deps.registry.env is Env.PROD


@pytest.mark.parametrize("env", [Env.DEV, Env.PROD, Env.SAMPLE], ids=["dev", "prod", "sample"])
def test_init_calls_config_from_providers_with_toml_provider(env: Env, _patch_runtime_deps: RuntimeDeps):
    MitupRuntime(env)

    call_args = _patch_runtime_deps.config.call_args
    providers = call_args.args
    toml_providers = [p for p in providers if isinstance(p, TomlConfigProvider)]
    assert len(toml_providers) == 1
    assert toml_providers[0].env == env


def test_init_configures_db(_patch_runtime_deps: RuntimeDeps):
    runtime = MitupRuntime(Env.DEV)

    _patch_runtime_deps.db.assert_called_once_with(runtime.config.db)


def test_init_configures_timezone_api(_patch_runtime_deps: RuntimeDeps):
    runtime = MitupRuntime(Env.DEV)

    _patch_runtime_deps.tz.assert_called_once_with(runtime.config.google_api)


def test_init_configures_metrics(_patch_runtime_deps: RuntimeDeps):
    runtime = MitupRuntime(Env.DEV)

    _patch_runtime_deps.metrics.assert_called_once_with(runtime.config.metrics)


# --- Build application ---


def test_builder_called_with_token(_patch_runtime_deps: RuntimeDeps):
    MitupRuntime(Env.DEV)

    _patch_runtime_deps.builder_instance.token.assert_called_once_with("fake-bot-token")


def test_builder_sets_context_types(_patch_runtime_deps: RuntimeDeps):
    MitupRuntime(Env.DEV)

    _patch_runtime_deps.builder_instance.context_types.assert_called_once()


def test_builder_sets_rate_limiter(_patch_runtime_deps: RuntimeDeps):
    MitupRuntime(Env.DEV)

    _patch_runtime_deps.builder_instance.rate_limiter.assert_called_once()


def test_builder_sets_per_user_update_processor(_patch_runtime_deps: RuntimeDeps):
    MitupRuntime(Env.DEV)

    _patch_runtime_deps.builder_instance.concurrent_updates.assert_called_once()
    (processor,) = _patch_runtime_deps.builder_instance.concurrent_updates.call_args.args
    assert isinstance(processor, PerUserUpdateProcessor)
    assert processor.max_concurrent_updates == 1  # MAX_CONCURRENT_UPDATES stays at 1 until #190


def test_bind_called_with_built_app(_patch_runtime_deps: RuntimeDeps):
    runtime = MitupRuntime(Env.DEV)

    _patch_runtime_deps.registry.bind.assert_called_once_with(runtime.app)


# --- Logging ---


@pytest.fixture
def _real_configure_logging() -> Generator[None]:
    """Opt the requesting test out of `_patch_runtime_deps`'s `configure_logging` stub so it
    exercises the production logging setup (it must also request `_restore_root_logging`).

    Production `configure_logging` runs `structlog.configure(cache_logger_on_first_use=True)`, so
    the first log emitted while building the runtime freezes each module-level
    `structlog.get_logger(__name__)` proxy onto the production `wrap_for_formatter` pipeline by
    caching its `bind`. That cache survives the deterministic reconfigure in `deterministic_structlog`,
    so a later test reusing a frozen proxy would emit a record carrying a live
    `_FixedFindCallerLogger` and crash xdist's report serialization. On teardown we drop every cached
    `bind` so each proxy re-binds against whatever pipeline is configured next.
    """
    yield
    for proxy in (obj for obj in gc.get_objects() if isinstance(obj, BoundLoggerLazyProxy)):
        proxy.__dict__.pop("bind", None)


@pytest.fixture
def _restore_root_logging() -> Generator[None]:
    """MitupRuntime configures real logging via configure_logging, which mutates the root logger's
    handlers and level. Snapshot and restore them so these tests don't leak into the rest of the suite.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


@pytest.mark.parametrize(
    "env, expected_renderer",
    [
        (Env.DEV, structlog.dev.ConsoleRenderer),
        (Env.PROD, structlog.processors.JSONRenderer),
        (Env.SAMPLE, structlog.processors.JSONRenderer),
    ],
    ids=["dev", "prod", "sample"],
)
def test_logging_installs_processor_formatter_handler_by_env(
    env: Env,
    expected_renderer: type[structlog.dev.ConsoleRenderer] | type[structlog.processors.JSONRenderer],
    _real_configure_logging: None,
    _patch_runtime_deps: RuntimeDeps,
    _restore_root_logging: None,
):
    """MitupRuntime configures logging so the root carries exactly one StreamHandler whose
    ProcessorFormatter renders through the env-selected final renderer (ConsoleRenderer in dev,
    JSONRenderer otherwise). This replaces the old RichHandler-in-dev / handlers=None contract.
    """
    MitupRuntime(env)

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    handler = handlers[0]
    assert type(handler) is logging.StreamHandler

    formatter = handler.formatter
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    # build_root_handler appends the final renderer last in the formatter's processors list.
    assert isinstance(formatter.processors[-1], expected_renderer)


def test_httpx_logger_set_to_warning(
    _real_configure_logging: None, _patch_runtime_deps: RuntimeDeps, monkeypatch: pytest.MonkeyPatch
):
    logger = logging.getLogger("httpx")
    monkeypatch.setattr(logger, "level", logger.level)

    MitupRuntime(Env.DEV)

    assert logger.level == logging.WARNING


@pytest.mark.parametrize(
    "env, expected_level",
    [
        (Env.DEV, logging.DEBUG),
        (Env.PROD, logging.WARNING),
        (Env.SAMPLE, logging.WARNING),
    ],
    ids=["dev", "prod", "sample"],
)
def test_ext_bot_logger_level_by_env(
    env: Env,
    expected_level: int,
    _real_configure_logging: None,
    _patch_runtime_deps: RuntimeDeps,
    monkeypatch: pytest.MonkeyPatch,
):
    logger = logging.getLogger("telegram.ext.ExtBot")
    monkeypatch.setattr(logger, "level", logger.level)

    MitupRuntime(env)

    assert logger.level == expected_level


# --- Run ---


def test_webhook_mode_builds_fastapi_and_runs_uvicorn(_patch_runtime_deps: RuntimeDeps):
    """Webhook mode disables PTB's internal updater, builds the FastAPI app via
    create_app with RunModes.WEBHOOK and a /telegram URL, and starts uvicorn with
    the configured host/port/workers/log_config. PTB's run_webhook must NOT run."""
    config = _build_config(
        run_mode=RunModes.WEBHOOK,
        domain="example.com",
        secret_token=SecretStr("my-secret"),
    )

    with (
        mock.patch("mitup_bot.app.Config.from_providers", return_value=config),
        mock.patch("mitup_bot.app.create_app") as mock_create_app,
        mock.patch("mitup_bot.app.uvicorn") as mock_uvicorn,
    ):
        fastapi_app = mock.MagicMock(name="fastapi_app")
        mock_create_app.return_value = fastapi_app

        runtime = MitupRuntime(Env.DEV)
        runtime.run()

    # Webhook mode must disable PTB's Updater so FastAPI feeds updates directly.
    _patch_runtime_deps.builder_instance.updater.assert_called_once_with(None)

    mock_create_app.assert_called_once()
    create_call = mock_create_app.call_args
    assert create_call.args[0] is runtime.app
    assert create_call.kwargs["secret_token"] == "my-secret"
    assert create_call.kwargs["run_mode"] is RunModes.WEBHOOK
    assert create_call.kwargs["webhook_url"].endswith("/telegram")
    assert create_call.kwargs["max_connections"] == 100  # default in BotConfig
    # metrics_client must be a MetricsClient instance — assert presence, not identity.
    assert "metrics_client" in create_call.kwargs

    # uvicorn.Config wraps the FastAPI app with the expected runtime parameters.
    mock_uvicorn.Config.assert_called_once()
    config_kwargs = mock_uvicorn.Config.call_args.kwargs
    assert config_kwargs["app"] is fastapi_app
    assert config_kwargs["host"] == "0.0.0.0"
    assert config_kwargs["port"] == 80  # BotConfig.listen_port default
    assert config_kwargs["workers"] == 1
    assert config_kwargs["log_config"] is None

    # uvicorn.Server is constructed with the Config and run() is invoked.
    mock_uvicorn.Server.assert_called_once_with(mock_uvicorn.Config.return_value)
    mock_uvicorn.Server.return_value.run.assert_called_once_with()

    # PTB's run_webhook is gone — make sure we did not regress to it.
    runtime.app.run_webhook.assert_not_called()


def test_polling_mode_builds_fastapi_and_runs_uvicorn(_patch_runtime_deps: RuntimeDeps):
    """Polling mode keeps PTB's default Updater, builds FastAPI with RunModes.POLLING
    and no webhook URL, and runs uvicorn. PTB's run_polling must NOT run."""
    config = _build_config(run_mode=RunModes.POLLING)

    with (
        mock.patch("mitup_bot.app.Config.from_providers", return_value=config),
        mock.patch("mitup_bot.app.create_app") as mock_create_app,
        mock.patch("mitup_bot.app.uvicorn") as mock_uvicorn,
    ):
        fastapi_app = mock.MagicMock(name="fastapi_app")
        mock_create_app.return_value = fastapi_app

        runtime = MitupRuntime(Env.DEV)
        runtime.run()

    # Polling mode must NOT disable PTB's Updater — it drives polling.
    _patch_runtime_deps.builder_instance.updater.assert_not_called()

    mock_create_app.assert_called_once()
    create_call = mock_create_app.call_args
    assert create_call.args[0] is runtime.app
    assert create_call.kwargs["run_mode"] is RunModes.POLLING
    # No webhook URL in polling mode — either absent or explicitly None.
    assert create_call.kwargs.get("webhook_url") is None

    mock_uvicorn.Config.assert_called_once()
    config_kwargs = mock_uvicorn.Config.call_args.kwargs
    assert config_kwargs["app"] is fastapi_app
    assert config_kwargs["host"] == "0.0.0.0"
    assert config_kwargs["port"] == 80  # BotConfig.listen_port default
    assert config_kwargs["workers"] == 1
    assert config_kwargs["log_config"] is None

    mock_uvicorn.Server.assert_called_once_with(mock_uvicorn.Config.return_value)
    mock_uvicorn.Server.return_value.run.assert_called_once_with()

    # PTB's run_polling is gone — make sure we did not regress to it.
    runtime.app.run_polling.assert_not_called()


def test_webhook_mode_missing_domain_raises(runtime: MitupRuntime):
    runtime.config = _build_config(
        run_mode=RunModes.WEBHOOK,
        secret_token=SecretStr("my-secret"),
    )

    with pytest.raises(ValueError, match="Domain must be set"):
        runtime.run()


def test_webhook_mode_missing_secret_token_raises(runtime: MitupRuntime):
    runtime.config = _build_config(
        run_mode=RunModes.WEBHOOK,
        domain="example.com",
    )

    with pytest.raises(ValueError, match="Secret token must be set"):
        runtime.run()
