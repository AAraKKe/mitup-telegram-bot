---
name: web-conventions
description: HTTP/web layer conventions for mitup_bot — the FastAPI app factory `create_app()`, the uvicorn server invocation inside `MitupRuntime.run()`, the `POST /telegram` webhook endpoint, the `@asynccontextmanager` lifespan that owns PTB's `initialize`/`start`/`set_webhook`/`stop`/`shutdown` sequence, secret-token validation, and FastAPI dependency injection with `Annotated[T, Depends(...)]`. Use this skill whenever the work touches `apps/bot/mitup_bot/web/`, the FastAPI/uvicorn/lifespan parts of `apps/bot/mitup_bot/app.py`, the PTB webhook lifecycle (`Application.builder().updater(None)`, `app.update_queue.put`, `app.process_update`, `bot.set_webhook`), adding new HTTP routes, OAuth callbacks, the `/telegram` endpoint, or any question about how uvicorn serves the bot — even when the task description doesn't mention FastAPI by name. Includes the hard rules that prevent silent breakage (uvicorn `workers=1`, `log_config=None`, never pass `allowed_updates`, never return non-2xx from `/telegram`).
user-invocable: false
---

# Web Conventions

The `apps/bot/mitup_bot/web/` package wraps the PTB `Application` with a FastAPI app served by uvicorn. The bot used to run on PTB's built-in webhook server; now uvicorn is the ASGI server and FastAPI owns request routing, with PTB lifecycle managed via FastAPI's lifespan. This skill captures the rules and rationale for everything in `apps/bot/mitup_bot/web/` and the runtime parts of `apps/bot/mitup_bot/app.py`.

## How the runtime is wired

`MitupRuntime.run()` (in `apps/bot/mitup_bot/app.py`) is the single entry point. It branches on `config.app.run_mode`:

| Mode | PTB builder | Lifespan does | Notes |
|---|---|---|---|
| `WEBHOOK` (prod) | `.updater(None)` — disables PTB's internal updater | `await initialize()` → `await start()` → `await bot.set_webhook(...)` | Updates arrive at `POST /telegram` and are handed to `app.update_queue`; the fetcher task started by `start()` drains the queue and dispatches them |
| `POLLING` (dev)  | default Updater kept | `await initialize()` → `await updater.start_polling()` → `await start()` | uvicorn still serves the FastAPI app so future non-Telegram endpoints can be exercised locally |

In both modes the runtime ends with `uvicorn.Server(uvicorn.Config(app=fastapi_app, host="0.0.0.0", port=config.bot.listen_port, workers=1, log_config=None, lifespan="on")).run()`. The lifespan owns the PTB lifecycle in full — startup and shutdown happen inside the context manager.

## Module layout (read these to understand the current state)

Rather than maintaining a list that goes stale, inspect `apps/bot/mitup_bot/web/` directly. At the time of writing it contains:

- `__init__.py` — re-exports `create_app` only.
- `app.py` — the FastAPI factory `create_app(...)`, the webhook and polling lifespan builders, the `run_shutdown_step` helper that isolates each shutdown step's failure.
- `dependencies.py` — the shared FastAPI DI getters (`get_ptb_application`, `get_webhook_secret`, `get_metrics_client`).
- `telegram.py` — the `POST /telegram` router, the secret-token validator, and the JSON-payload parser.
- `patreon.py` — the Patreon OAuth callback (`GET /patreon/callback`) and membership webhook (`POST /patreon/webhook`) routers.
- `templates/` — HTML templates rendered by `patreon.py`'s browser-facing result pages.

Anything new you see beyond this list is newer than this document — read the module docstring or its imports to understand purpose.

## Request flow for `POST /telegram`

```
Telegram → NLB (TCP, TLS terminates) → ECS container :80 → uvicorn → FastAPI → telegram_webhook()
  1. validate_secret() — secrets.compare_digest against config.bot.secret_token. 403 on miss/mismatch.
  2. json.loads(body) — 204 + WEBHOOK_MALFORMED_UPDATE on JSONDecodeError.
  3. Update.de_json(payload, bot=ptb_app.bot) — 204 + WEBHOOK_MALFORMED_UPDATE on parse failure.
  4. await ptb_app.update_queue.put(update) — hand off to PTB's update processor; do NOT process in-request.
  5. Return 204 No Content. Always — immediately, before the update is processed.
```

The endpoint enqueues rather than calling `process_update()` directly, for two reasons:

- **Concurrency is bounded.** The `concurrent_updates` semaphore is applied only by the fetcher task that `Application.start()` runs to drain `update_queue`. A direct `process_update()` call bypasses that entirely, so concurrency would be decided by uvicorn instead of PTB — effectively unbounded. Our webhook lifespan already calls `await ptb_app.start()`, so that fetcher task is running and consuming the queue.
- **Telegram never times out waiting on processing.** Returning 204 the instant the update is queued frees the HTTP request. Calling `process_update()` in-request holds the connection open for the full handler runtime, so a slow handler can make Telegram time out and re-deliver the same update.

Processing failures are now handled out-of-request by PTB's error handler (`error_handler.py`), not by a `try/except` in the endpoint. The queue is unbounded (`maxsize=0`), so `put()` never blocks.

## Hard rules — these will burn you if ignored

- **`uvicorn` must run with `workers=1`.** PTB's `Application` holds in-process state (handlers, conversation tracking, rate limiter, JobQueue) that is not multi-process safe. A second worker would silently corrupt state.
- **`uvicorn` must be invoked with `log_config=None`.** `mitup_bot.logging_config.configure_logging(...)`, called from `MitupRuntime.__init__`, configures the root logger before uvicorn starts. Letting uvicorn install its default logging config overrides that and breaks the Rich handler in dev / the JSON formatter in prod.
- **The `/telegram` endpoint must NEVER return non-2xx for application errors.** Telegram retries on any non-2xx. Returning 5xx on a poison-pill update creates a retry storm and floods the bot. Wrap every parse step AND the `update_queue.put` call in `try/except`, log, and return 204 — enqueuing an unbounded queue shouldn't raise, but a broken event loop or app state could, and the always-2xx contract must hold in code, not just by assumption. Processing exceptions can no longer surface in the request (the update is dispatched off the queue, out of band) — they belong to PTB's error handler.
- **The endpoint must enqueue, not process in-request.** Call `await ptb_app.update_queue.put(update)` and return 204 immediately; never `await ptb_app.process_update(update)` from the handler. The `concurrent_updates` semaphore is enforced only by the fetcher task draining the queue, and an immediate 204 prevents Telegram from timing out and re-delivering while a handler runs. See "Request flow" above for the full rationale.
- **Secret-token validation must use `secrets.compare_digest`, not `==`.** Constant-time comparison prevents timing attacks. Never log the received token value on mismatch — log only `request.client.host`.
- **`set_webhook` must NOT pass `allowed_updates`.** Per the Telegram Bot API, omitting `allowed_updates` preserves the previous setting. The bot's current subscription includes update types (e.g. inline queries) that aren't in Telegram's "empty list" default. Passing an explicit list would silently drop them on the next deploy.
- **PTB `initialize()` and `start()` must complete before updates are enqueued.** `Update.de_json` needs the bot (`initialize`), and the fetcher task that drains `update_queue` is started by `start()` — enqueue before it runs and updates pile up unprocessed. uvicorn's `lifespan="on"` enforces this ordering — never accept HTTP traffic before the lifespan context is entered.
- **Lifespan ordering is fixed.** Webhook startup: `initialize → start → set_webhook`. Webhook shutdown: `stop → shutdown`. Polling startup: `initialize → updater.start_polling → start`. Polling shutdown: `updater.stop → stop → shutdown`. Tests pin these orders — if you change them, update the lifespan tests in the same MR.
- **Per-step shutdown isolation is a contract.** Each shutdown step runs through `run_shutdown_step(...)`. A failure in one step emits `MetricKey.LIFESPAN_SHUTDOWN_FAILED` and continues to the next step. Don't introduce shutdown steps that abort the rest on failure.

## Two ports, not one

The runtime cares about two distinct ports — keep them straight:

- **`config.bot.port`** (default `443`) — the *public-facing* port used to build the webhook URL passed to Telegram (`https://{domain}:{port}/telegram`). Matches the NLB listener.
- **`config.bot.listen_port`** (default `80`) — the port uvicorn binds to inside the container. Matches the ECS task `containerPort`. Changing this requires an infra change.

Don't conflate them. The webhook URL is built from `bot.port`; uvicorn binds to `bot.listen_port`.

## Dependency injection (DI)

FastAPI's `Depends(...)` provides typed access to per-request state. The pattern in `telegram.py`:

```python
def get_ptb_application(request: Request) -> Application:
    return request.app.state.ptb_app


@router.post("/telegram", status_code=204)
async def telegram_webhook(
    request: Request,
    ptb_app: Annotated[Application, Depends(get_ptb_application)],
    ...
): ...
```

Why we use this instead of `request.app.state.X` inline:
- **Type safety** — Starlette's `app.state` returns `Any`. `Depends(get_ptb_application)` narrows to `Application` at the parameter level.
- **Test override hook** — FastAPI's `app.dependency_overrides[get_ptb_application] = lambda: fake_app` works without mutating `app.state`. We don't use it today (tests mutate state directly), but the door stays open.

Where DI getters live: **`dependencies.py`**. `telegram.py` and `patreon.py` both depend on `get_ptb_application` and `get_metrics_client`, so the getters live in a shared module rather than alongside a single consumer.

## Adding a new HTTP route

1. Create a new module in `apps/bot/mitup_bot/web/` (e.g. `apps/bot/mitup_bot/web/foo.py`).
2. Define a router: `router = APIRouter()`.
3. Add the endpoint with a typed signature, using `Annotated[T, Depends(...)]` for any per-request state you need.
4. If the endpoint needs new state that isn't already on `app.state`, add it in `create_app(...)` in `app.py` AND provide a typed getter beside the endpoint.
5. Register the router in `create_app(...)` with `app.include_router(foo.router)`.
6. Add tests under `tests/bot/web/` mirroring the structure of `test_telegram.py`: use `httpx.AsyncClient` with `ASGITransport`, mock `ptb_app` and any other state via `build_test_web_app(...)` helpers.

## Response codes

- `POST /telegram` returns **204 No Content** on every path. Telegram only reads the status code; a 2xx is "delivered". Returning a body wastes bandwidth and reads as if we're using the inline-method response feature (we aren't).
- For new endpoints intended for *users* (not Telegram), pick conventional status codes — 200 with a JSON body is fine when there's something to return.

## Things to remember about graceful shutdown

When uvicorn receives SIGTERM (Docker stop, ECS task drain, dev `^C`):
1. uvicorn stops accepting new requests.
2. In-flight requests are given `timeout_graceful_shutdown` to finish.
3. The lifespan's `finally` block runs: PTB `stop` → `shutdown` (and `updater.stop` first, in polling).
4. The process exits.

If you add long-running async tasks (background loops, polling for external services), make sure they react to the lifespan exiting — otherwise uvicorn will hang and eventually SIGKILL them.
