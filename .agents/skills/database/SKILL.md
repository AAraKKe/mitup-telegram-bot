---
name: database
description: Database layer conventions. Auto-load when writing or modifying models, sessions, migrations, or the @with_session decorator.
user-invocable: false
---

# Database

## Engine and sessions

The database layer lives in `libs/data/mitup_bot/db.py`. It uses SQLAlchemy's **async engine** (psycopg 3 driver) with SQLModel's `AsyncSession`, managed through an `async_sessionmaker` configured at startup via `configure_db()`. Pool sizing comes from `DbConfig` (`pool_size` / `max_overflow` / `pool_timeout`), and the update-concurrency cap (`bot.concurrent_updates`) must fit inside it — `Config` validates `cap ≤ pool_size + max_overflow − POOL_CONNECTION_HEADROOM` at startup, keeping connections free for the job queue and reconcile transactions.

**Pool observability:** when `configure_db()` receives a `metrics_client` (the bot runtime always passes one; CLI commands don't), pool events emit the `DbPool*` metrics defined in `MetricKey`, and `begin()` eagerly checks out the transaction's connection so pool wait time and pool timeouts are measured at transaction start, flushing the accumulated records once per transaction.

**Never create sessions manually.** Use the session-injecting decorator instead.

## Session decorator

`with_session` injects an `AsyncSession` as the first positional argument and wraps the call in a transaction (commit on success, rollback on exception). The decorated function must be async; every session I/O method (`exec`, `flush`, `refresh`, `delete`, `get`, `commit`, `rollback`, `begin_nested`) must be awaited (`begin_nested` is used with `async with`).

```python
from mitup_bot.db import with_session
from sqlmodel.ext.asyncio.session import AsyncSession

@with_session
async def my_handler(session: AsyncSession, update: Update, context: MitupContext) -> int:
    user = (await session.exec(select(User).where(User.id == user_id))).first()
    ...

# Call without the session argument — the decorator supplies it:
await my_handler(update, context)
```

### Write mode: `begin_write` and `@with_session(write=True)`

Code that **mutates state and then fans out over Telegram** (edit meeting messages, notify users) uses the write lifecycle. The `db.begin_write(api)` async context manager owns the whole two-phase lifecycle — ordering is infrastructure, not author discipline:

1. The api is switched into capture mode: every `api.*` call enqueues a plain-data snapshot instead of executing (see the `api-wrapper` skill).
2. The body runs and the transaction **commits**, releasing the pooled connection and any per-meeting row lock.
3. The queued Telegram calls execute in order, and their DB fix-ups (dead message rows, unreachable users) are applied in one short reconcile transaction.

Handlers use it through `@with_session(write=True)`, a thin wrapper that captures on `context.api`; non-handler code (CLI batch jobs) has no `MitupContext` and drives the context manager directly, one `async with db.begin_write(api)` block per critical section — per meeting or per joined link, whichever row carries the job's flag (see the recurrent-event jobs in `apps/events/mitup_bot/events/`, e.g. `inactive_meetings.py`). Bodies keep their linear style — only the execution time of the api calls moves. Rules of thumb:

- **Broadcast ⇒ write mode.** Every handler that mutates state and then fans out over Telegram — whether it takes the per-meeting row lock (participant, capacity, or meeting-existence mutations) or just calls `update_meeting_messages` / notifies users — uses write mode; locking paths MUST commit before their fan-out. Plain `@with_session` stays for read-only handlers.
- No live session ever crosses into the fan-out: `update_meeting_messages` renders plain-data payloads at enqueue time and takes no session (the old `session` parameter is gone — the reconcile transaction owns dead-message cleanup for every caller).
- Drop defensive "flush before send" calls: commit-before-drain provides fail-early ordering structurally.
- `context.api.immediate.X(...)` is the escape hatch for a call that must run pre-commit (its failure aborts the transaction). Keep usages rare and greppable.
- If the body raises, the queue is discarded with the rolled-back transaction — nothing about aborted state is rendered.
- **The reconcile behavior is registered, not built in.** `db.py` knows the api and its outbox only through the structural `WriteApi` / `OutboxProtocol` protocols; the model-aware fix-up logic lives in `libs/telegram/mitup_bot/reconcile.py` and is wired via `reconcile.register_outbox_reconciler()` at startup. `begin_write` refuses to run without a registered reconciler, so every new process entry point that runs write-mode critical sections must call it once (the bot runtime, the recurrent-events CLI, and the test suite's root conftest already do).

### `racy_flush` — the single racy-write primitive

Inserts that can lose a uniqueness race against a concurrent transaction go through `db.racy_flush`:

```python
link = await racy_flush(session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT)
if link is None:
    ...  # a concurrent writer already inserted the row — treat as idempotent no-op
```

The builder callable runs **inside** `begin_nested()` — construct the racy rows in the builder, never before the call (construction is what makes rows session-pending through relationship cascades; building them inside the savepoint is what lets a clash roll back cleanly). The helper adds the built row explicitly (SQLAlchemy 2.0 does not cascade backref-only associations), flushes, narrows `IntegrityError` to the named constraint, and on a clash rolls the savepoint back and reloads exactly the attributes the rollback unloaded — so in-memory collections (including `lazy="raise"` ones) reflect committed state. Any other `IntegrityError` re-raises. Before checking capacity ahead of the call, make sure the meeting was loaded `for_update=True` so the pre-check cannot go stale.

## Lazy loading is forbidden at runtime

The async engine cannot run implicit lazy loads: touching an unloaded relationship or expired attribute raises `MissingGreenlet`. The strategy:

- Relationships traversed in plain Python by model properties are `lazy="selectin"` (via `sa_relationship_kwargs`): `User.settings`, `Meetup.owner/messages/joined_links`, `JoinedUsers.user/invited_by/meetup`, `Message.meetup`.
- `User.meetups` and `User.joined_links` are `lazy="raise"` (eager both ways would recurse across the whole social graph): any unloaded access raises `InvalidRequestError` immediately instead of a prod-only `MissingGreenlet`. Load them through the sanctioned routes only — `User.by_tg_user_id(..., load_collections=True)` applies explicit `selectinload` options (covers every current-user path), and freshly flushed users get `await session.refresh(user, ["joined_links", "meetups"])` (see `register_default_user`). The raise only fires on a session-bound instance: a transient fixture object hands back an empty list instead, so unit tests built on `MockDbSession` cannot catch a missing load — the `db_test` suite is what pins these paths.
- **Relationship-level selectin does not cascade through a load-path cycle.** When the eager-load path reaches a mapper already on it, SQLAlchemy stops implicit eager loading at that hop. A user-rooted load cycles `User -> Meetup -> JoinedUsers -> User`, so each meeting's `owner` and its participants' `user`/`invited_by` behind `meetups`/`joined_links` are dropped even though those relationships are `lazy="selectin"` on their mapper. Loads rooted at a user that render participant lists must therefore **spell out the full loader chains** to the leaves the view reads — see `user_collection_loaders` in `models/users.py` and the export statements in `handlers/privacy/data_export.py`. Meetup-rooted loads (`Meetup.by_id`) repeat no mapper and are unaffected. `User.by_tg_user_id` gates the cost: `load_collections=True` loads the one-hop collections plus `joined_links -> meetup` (what the list screens need), and `load_participants=True` adds the deep participant/owner chains on top — call the classmethod directly with both only from a handler that renders a full meeting card straight off `user.meetups`/`user.joined_links` rather than re-loading through `Meetup.by_id`. The inline query is the sole such caller; `guards.current_user` exposes `load_collections` only.
- After flushing a **new** instance, its never-touched collections are still unloaded — `await session.refresh(obj, ["<relationship>"])` before rendering anything that traverses them (see the create-meeting handler).
- The session factory sets `expire_on_commit=False`, so post-commit attribute access never triggers a load.

## Per-meeting row locks

Meeting capacity and waiting-list logic is computed in Python over the loaded `joined_links` collection, so cross-user races (two joins on the last slot, leave-with-promotion vs join) must serialize on the database. The `meetups` row is the per-meeting mutex:

- **Every participant- or capacity-mutating path** loads the meeting via `Meetup.by_id(session, id, for_update=True)` (directly or through `guards.meeting(..., lock=True)`) **before** reading any capacity/waiting-list state. This issues `SELECT … FOR UPDATE` on the meetups row plus `populate_existing`, so the post-lock read overwrites any stale identity-mapped state pulled in by the current-user eager loads.
- **`populate_existing` unloads the acting user's collections.** It re-hydrates every entity the statement touches, including the identity-mapped `User` reached through `owner`/`joined_links`, which resets `meetups`/`joined_links` to unloaded. A locking handler that also reads those collections must re-load them itself with `await session.refresh(user, ["meetups", "joined_links"])` after the locked load — the row lock is already held, so the re-read is race-safe. `handle_join_leave_operation` is the pattern. Handlers that took no collections (the default from `guards.current_user`) need nothing.
- **Read-only paths** (show views, lists, inline queries, confirmation prompts) must NOT take the lock.
- **Lock ordering:** meeting row first, then anything else. Never lock two meetings in one transaction — every handler operates on a single meeting, which keeps the deadlock surface at zero.
- `FOR UPDATE` applies only to the meetups row; the `selectin` follow-up loads run unlocked. That's correct: the row lock is what serializes writers, the link rows don't need locking.
- Unconditional writes that make no participant-dependent decision (e.g. reactivation setting `active = True`) don't need the explicit lock — the flush-time UPDATE acquires it.
- **The lock is never held across Telegram I/O:** every locking path runs under the write lifecycle (`@with_session(write=True)` in handlers, `db.begin_write` in batch jobs), which commits (releasing the lock) before the queued fan-out executes. A new locking path must use write mode too; `tests/data/db_behavior/test_commit_before_fanout.py` and `tests/data/db_behavior/test_events_write_lifecycle.py` pin the release-at-commit property on real Postgres.
- **Batch jobs are writers too:** a scheduled job that mutates meetings (e.g. the expiration sweep in `apps/events/mitup_bot/events/inactive_meetings.py`) takes the same per-meeting lock, re-checks its decision under the lock (the unlocked candidate sweep only nominates), and wraps each meeting in its own `db.begin_write` block so locks are held briefly and a crash keeps the deactivations already committed.

## Models

All SQLModel table models live in `libs/data/mitup_bot/models/` and use SQLModel. Inspect `libs/data/mitup_bot/models/__init__.py` for the current list of exported models.

Key patterns:

- **Models are PTB-free.** `import mitup_bot.models` must succeed without python-telegram-bot installed — the migrations Lambda loads model metadata for alembic in a PTB-free image. Never import `telegram` (or anything that transitively reaches it, e.g. `mitup_bot.utils.entities` / `mitup_bot.utils.messages` / the `mitup_bot.utils` package init) at runtime in `libs/data/mitup_bot/models/`; annotation-only uses go under `TYPE_CHECKING`, and user-facing text rendering belongs in `views/` (see `views/meeting_text.py`).

- All models inherit from `BaseModel` (in `base_model.py`) which provides the `db_id` property.
- Models with JSON columns that need mutation tracking extend `MutableModel` (in `mutable_model.py`).
- Pydantic models (e.g., `MeetupLocation`, `MessageButtons`) are used for structured JSON fields and are not SQLModel tables.

When adding a new model:

1. Create the model class in the appropriate file under `libs/data/mitup_bot/models/`.
2. Export it from `libs/data/mitup_bot/models/__init__.py`.
3. Generate an Alembic migration (see [Migrations](#migrations) below).
4. Add test helpers in `tests/helpers/` if the model is referenced in 2 or more test modules or requires more than 2 constructor arguments.

## Migrations

Database migrations use [Alembic](https://alembic.sqlalchemy.org/). Migration scripts live in `libs/data/mitup_bot/migrations/versions/`.

Day-to-day commands when running the bot locally:

```bash
uv run mb db migrate up    # Apply pending migrations
uv run mb db migrate down  # Roll back one migration
uv run mb db migrate validate   # Validate migration graph integrity
```

When a model change needs a new migration, invoke the `new-migration` skill — it walks through scaffolding the revision file, writing `upgrade()` / `downgrade()` by hand (autogenerate is disallowed), and validating that both paths apply cleanly. Don't repeat the walkthrough here; that skill owns it.

### Deploy ordering and the shared database

Migrations run (`alembic upgrade head`, via the migrations Lambda) **before** the new app images roll, against the still-running previous code — and a rolled-back ECS deploy does **not** undo them, so it runs the *previous* image against the *new* schema. Every migration must therefore be backward-compatible with the currently-deployed image.

The bot and the recurrent-events services share **one** database. A schema or data change must be safe for **both** at once, not just the service you happen to be editing.

Breaking changes must be split across separate releases — **expand → migrate → contract**: add the new (nullable) shape, backfill, and dual-write first; switch reads/writes in a later release; drop or rename only once nothing references the old shape. Never drop or rename a column in the same release that stops using it.

## Automatic timestamps

PostgreSQL triggers (`set_created_time()`, `set_updated_time()`) defined in migration `65b4c46d9141` set `CURRENT_TIMESTAMP` on insert and update respectively. Application code never assigns these fields.

Model classes declare them as `dt.datetime | None = None` because Python cannot know about DB-level triggers at import time. In production every persisted row has non-`None` values. Test fixtures (e.g., `create_meetup`) supply a default `created_time` to mirror this invariant.
