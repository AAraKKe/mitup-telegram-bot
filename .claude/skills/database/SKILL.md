---
name: database
description: Database layer conventions. Auto-load when writing or modifying models, sessions, migrations, or the @with_session decorator.
user-invocable: false
---

# Database

## Engine and sessions

The database layer lives in `mitup_bot/db.py`. It uses SQLAlchemy's **async engine** (psycopg 3 driver) with SQLModel's `AsyncSession`, managed through an `async_sessionmaker` configured at startup via `configure_db()`. Pool sizing comes from `DbConfig` (`pool_size` / `max_overflow` / `pool_timeout`).

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

## Lazy loading is forbidden at runtime

The async engine cannot run implicit lazy loads: touching an unloaded relationship or expired attribute raises `MissingGreenlet`. The strategy:

- Relationships traversed in plain Python by model properties are `lazy="selectin"` (via `sa_relationship_kwargs`): `User.settings`, `Meetup.owner/messages/joined_links`, `JoinedUsers.user/invited_by/meetup`, `Message.meetup`.
- `User.meetups` and `User.joined_links` are `lazy="raise"` (eager both ways would recurse across the whole social graph): any unloaded access raises `InvalidRequestError` immediately instead of a prod-only `MissingGreenlet`. Load them through the sanctioned routes only — `User.by_tg_user_id` applies explicit `selectinload` options (covers every current-user path), and freshly flushed users get `await session.refresh(user, ["joined_links", "meetups"])` (see `register_default_user`).
- After flushing a **new** instance, its never-touched collections are still unloaded — `await session.refresh(obj, ["<relationship>"])` before rendering anything that traverses them (see the create-meeting handler).
- The session factory sets `expire_on_commit=False`, so post-commit attribute access never triggers a load.

## Per-meeting row locks

Meeting capacity and waiting-list logic is computed in Python over the loaded `joined_links` collection, so cross-user races (two joins on the last slot, leave-with-promotion vs join) must serialize on the database. The `meetups` row is the per-meeting mutex:

- **Every participant- or capacity-mutating path** loads the meeting via `Meetup.by_id(session, id, for_update=True)` (directly or through `guards.meeting_accessible(..., for_update=True)`) **before** reading any capacity/waiting-list state. This issues `SELECT … FOR UPDATE` on the meetups row plus `populate_existing`, so the post-lock read overwrites any stale identity-mapped state pulled in by the current-user eager loads.
- **Read-only paths** (show views, lists, inline queries, confirmation prompts) must NOT take the lock.
- **Lock ordering:** meeting row first, then anything else. Never lock two meetings in one transaction — every handler operates on a single meeting, which keeps the deadlock surface at zero.
- `FOR UPDATE` applies only to the meetups row; the `selectin` follow-up loads run unlocked. That's correct: the row lock is what serializes writers, the link rows don't need locking.
- Unconditional writes that make no participant-dependent decision (e.g. reactivation setting `active = True`) don't need the explicit lock — the flush-time UPDATE acquires it.
- **Interim caveat:** until #188 restructures handlers to commit before Telegram fan-outs, the lock is held across the fan-out. Acceptable short-lived state, by design.

## Models

All SQLModel table models live in `mitup_bot/models/` and use SQLModel. Inspect `mitup_bot/models/__init__.py` for the current list of exported models.

Key patterns:

- All models inherit from `BaseModel` (in `base_model.py`) which provides the `db_id` property.
- Models with JSON columns that need mutation tracking extend `MutableModel` (in `mutable_model.py`).
- Pydantic models (e.g., `MeetupLocation`, `MessageButtons`) are used for structured JSON fields and are not SQLModel tables.

When adding a new model:

1. Create the model class in the appropriate file under `mitup_bot/models/`.
2. Export it from `mitup_bot/models/__init__.py`.
3. Generate an Alembic migration (see [Migrations](#migrations) below).
4. Add test helpers in `tests/helpers/` if the model is referenced in 2 or more test modules or requires more than 2 constructor arguments.

## Migrations

Database migrations use [Alembic](https://alembic.sqlalchemy.org/). Migration scripts live in `mitup_bot/migrations/versions/`.

Day-to-day commands when running the bot locally:

```bash
hatch run dev:migrations-upgrade    # Apply pending migrations
hatch run dev:migrations-downgrade  # Roll back one migration
hatch run dev:validate-migrations   # Validate migration graph integrity
```

When a model change needs a new migration, invoke the `new-migration` skill — it walks through scaffolding the revision file, writing `upgrade()` / `downgrade()` by hand (autogenerate is disallowed), and validating that both paths apply cleanly. Don't repeat the walkthrough here; that skill owns it.

## Automatic timestamps

PostgreSQL triggers (`set_created_time()`, `set_updated_time()`) defined in migration `65b4c46d9141` set `CURRENT_TIMESTAMP` on insert and update respectively. Application code never assigns these fields.

Model classes declare them as `dt.datetime | None = None` because Python cannot know about DB-level triggers at import time. In production every persisted row has non-`None` values. Test fixtures (e.g., `create_meetup`) supply a default `created_time` to mirror this invariant.
