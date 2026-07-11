# CLI Test Patterns

## Overview

Each app owns one CLI entry module — `apps/bot/mitup_bot/bot_cli.py` (`launch`) and `apps/events/mitup_bot/events_cli.py` (`recurrent-events`); the rails tool's is `tools/rails-migration/mitup_bot/migration/cli.py`. Tests live in `tests/cli/` (bot) and `tests/events/` (events). CLI tests typically use `click.testing.CliRunner` and heavy patching since the commands orchestrate configuration, DB, metrics, and the bot.

Both app entry modules expose a Click **group** named `cli`; the actual command is a subcommand of it (`launch`, `recurrent-events`). Import the subcommand callback directly when a test invokes it in isolation, or invoke the group with the subcommand name.

## Pattern: testing a Click CLI command

```python
from click.testing import CliRunner
from unittest.mock import MagicMock, patch

from mitup_bot.events_cli import recurrent_events


def test_cli_invokes_with_defaults():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.events.service.db") as mock_db,
    ):
        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        result = runner.invoke(recurrent_events, [])

        assert result.exit_code == 0, result.output
        mock_config_cls.assert_called_once()
```

Key points:
- Patch orchestration where it executes (e.g. `mitup_bot.events.service.db`), not at the thin entry module — the command just forwards to the service.
- The bot's `launch` instantiates `MitupRuntime`; patch it at `mitup_bot.bot_cli.MitupRuntime`.
- Assert `result.exit_code == 0, result.output` to get the output on failure.

## Recurrent events tests

The events service (`apps/events/mitup_bot/events/service.py`) runs periodic async tasks; its tests live in `tests/events/test_service.py`. The individual job modules and their tests live alongside it under `apps/events/mitup_bot/events/` and `tests/events/`. The thin `recurrent-events` Click command lives in `apps/events/mitup_bot/events_cli.py` and just parses options before delegating to `service.run_events`. Tests cover:

1. **`IntervalsConfiguration.get()`** — Parametrized test that each `EventType` maps to the correct config field.
2. **`launch_event()`** — Split into async and sync event types. Async events use `AsyncMock`, sync events use regular `MagicMock`.
3. **`handle_maintainance()`** — Parametrized over success/fault/leaked-connections scenarios. Uses `StubMetrics` directly (not `StubMetricsEngine`) because there's no handler context.
4. **`run_periodic()`** — Uses `CancelledError` to break out of the infinite loop after one iteration.
5. **`run_all_tasks()`** — Patches `run_periodic` to verify all event types are created.
6. **CLI entry point** — Import `recurrent_events` from `mitup_bot.events_cli`, but patch the orchestration (`Config`, `db`, `build_bot`, `run_all_tasks`, …) at `mitup_bot.events.service`, where `run_events` executes them. Tests that Click options are parsed and passed through correctly.

### Testing async periodic loops

Use `CancelledError` to break out of infinite async loops:

```python
from asyncio import CancelledError

async def test_run_periodic_runs_event():
    with (
        patch("...asyncio.sleep", side_effect=[None, CancelledError()]),
        patch("...handle_maintainance", new_callable=AsyncMock) as mock_handle,
    ):
        with pytest.raises(CancelledError):
            await run_periodic(60, EventType.USER_CLEANUP, api, time_before_start=0)

        mock_handle.assert_awaited_once_with(EventType.USER_CLEANUP, api)
```

### Standalone StubMetrics (no context)

CLI tests use `StubMetrics` directly because there's no Telegram update context:

```python
from tests.helpers import StubMetrics

stub = StubMetrics()
# ... pass stub as the metrics logger to the function under test ...
stub.assert_metrics_emited(
    [MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [0, AnyFloat(), 0],
    [Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
    dimensions={"EventType": event_type.value},
)
```

Note: `StubMetrics.assert_metrics_emited` requires explicit `dimensions` since no handler identity is attached automatically outside a `MitupContext` (and handler identity rides as EMF properties, never as dimensions — issue #205).
