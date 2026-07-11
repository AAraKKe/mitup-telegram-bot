---
icon: material/database-outline
---

# Data and migrations

The data layer is the part of Mitup most likely to surprise you. It looks like ordinary SQLModel, but four rules bend the code in ways that only make sense once someone points them out. This page points them out. When you need the full detail, follow the links to the [database skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/database/SKILL.md), which is the authority.

Everything here lives in [`mitup_bot/db.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/data/mitup_bot/db.py) and the models under [`mitup_bot/models/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/data/mitup_bot/models).

## The `@with_session` decorator

You never open a session by hand. A handler asks for one by wearing the decorator, and the session arrives as its first positional argument:

```python
@with_session
async def my_handler(session: AsyncSession, update: Update, context: MitupContext) -> int:
    user = (await session.exec(select(User).where(User.id == user_id))).first()
    ...
```

The decorator wraps the call in a transaction: it commits when the function returns cleanly and rolls back if it raises. Callers invoke `my_handler(update, context)` without the session. The decorator supplies it. Because the engine is async, every session call (`exec`, `flush`, `refresh`, `get`, `begin_nested`) must be awaited.

## Write mode: your send happens after the commit

Handlers that change state and then message people over Telegram (edit a meeting card, notify participants) use `@with_session(write=True)`. This is the surprising one, and it runs in two phases so ordering is guaranteed by infrastructure rather than by remembering to do it in the right order:

1. **Capture.** `context.api` switches into capture mode. Every `context.api.*` call records a plain-data snapshot of the intended Telegram call instead of sending it.
2. **Commit.** The handler finishes and the transaction commits, which releases the pooled connection and any row lock it held.
3. **Drain.** Only now do the captured Telegram calls run, in order.
4. **Reconcile.** Fix-ups discovered while draining (messages Telegram reports gone, users who blocked the bot) are applied in one short follow-up transaction.

So inside a write-mode handler your code reads top to bottom like normal, but the sends do not happen where you wrote them. They happen after the commit. If the handler raises, the queued calls are discarded along with the rolled-back transaction, so nothing about a half-finished state ever reaches a user.

!!! note "One exception"

    `context.api.immediate.X(...)` runs a call before the commit, so its failure aborts the transaction. Keep those rare and easy to grep for. The rules for when to reach for it live in the [database skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/database/SKILL.md).

## No lazy loading

The async engine cannot run an implicit lazy load. Touch an unloaded relationship and you get a `MissingGreenlet` error, not a quiet extra query. The models handle this up front instead of hoping:

* Relationships that model properties walk in plain Python are `lazy="selectin"`, so they load eagerly alongside their parent. `Meetup.owner`, `JoinedUsers.user`, and `Message.meetup` are in this group.
* `User.meetups` and `User.joined_links` are `lazy="raise"`. Loading both directions eagerly would pull in the whole social graph, so an unsanctioned access raises a clear error at once rather than a production-only `MissingGreenlet`. Load them through the sanctioned routes: `User.by_tg_user_id` applies explicit `selectinload`, and a freshly flushed row gets an explicit `await session.refresh(user, [...])`.
* After you flush a brand-new instance, its never-touched collections are still unloaded. Call `await session.refresh(obj, ["<relationship>"])` before rendering anything that traverses them.

The session factory sets `expire_on_commit=False`, so reading an attribute after the commit does not trip a reload either.

## Per-meeting row locks

Capacity and waiting-list logic is computed in Python over the loaded participant list, so two people grabbing the last slot at once would both see it free. The fix is to serialize writers on the database: the `meetups` row is the per-meeting mutex.

Every path that changes participants or capacity loads the meeting with `Meetup.by_id(session, id, for_update=True)` before reading any capacity state. That issues `SELECT ... FOR UPDATE` on the meetups row (plus `populate_existing`, so the locked read overwrites any stale copy the earlier eager loads pulled in). Read-only paths (views, lists, confirmation prompts) must not take the lock.

Two rules keep deadlocks at zero: lock the meeting row first and nothing else before it, and never lock two meetings in one transaction. Every handler works on a single meeting, so this stays natural.

The lock is never held across a Telegram send. Locking paths always run under `@with_session(write=True)`, which commits (and so releases the lock) before the drain phase does any messaging.

## Migrations are hand-written

Migrations use [Alembic](https://alembic.sqlalchemy.org/), and revisions live in [`mitup_bot/migrations/versions/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/libs/data/mitup_bot/migrations/versions). The surprise here is what you do not do: **autogenerate is not used.** Every `upgrade()` and `downgrade()` is written by hand so each schema change is deliberate and reviewable.

Day-to-day commands when running the bot locally:

```bash
uv run mb db migrate up        # apply pending migrations
uv run mb db migrate down      # roll back one migration
uv run mb db migrate validate  # check the migration graph
```

When a model change needs a migration, run the `/new-migration` skill. It scaffolds the revision, walks you through writing both directions by hand, and flags the things that bite: a missing `server_default` on a new non-nullable column, the trigger-managed `created_time` and `updated_time` columns that application code never sets, and a `downgrade()` that has to reverse everything `upgrade()` did. The [new-migration skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/new-migration/SKILL.md) owns that walkthrough.
