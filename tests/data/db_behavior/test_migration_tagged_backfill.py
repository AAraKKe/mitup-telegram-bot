import contextlib
import html
import importlib.util
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import Meetup, Settings, User

pytestmark = pytest.mark.db_test

MIGRATION_PATH = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(db.__file__).parent / "migrations" / "versions" / "6d469cde828e_add_tagged_title_and_description_to_.py"
)


def load_backfill_sql() -> str:
    """Load the exact UPDATE the migration runs, so this test breaks if that statement changes.

    The revision module can't be imported by dotted path (its name starts with a digit), so load it
    from its file location and read the constant ``upgrade()`` itself executes.
    """
    spec = importlib.util.spec_from_file_location("_migration_6d469cde828e", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BACKFILL_TAGGED_COLUMNS_SQL


BACKFILL_SQL = load_backfill_sql()

NASTY_TEXT = """<b>hi</b> & "quotes" 'apostrophes' <3"""


def make_meetup(title: str, owner: User, description: str | None = None) -> Meetup:
    return Meetup(
        title=title,
        description=description,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
    )


@contextlib.asynccontextmanager
async def committed_probe_rows(tg_user_id: int, build: Callable[[User], list[Meetup]]) -> AsyncIterator[list[int]]:
    """Commit a probe user plus the meetups ``build`` creates for it, yield the meetup ids, and
    tear everything down afterwards.

    Every transaction in this module is short-lived and committed: the backfill UPDATE is
    whole-table, so it row-locks every NULL-tagged meetup in the shared container — executing it
    from a transaction that stays open (like the session-scoped ``db_session``) holds those locks
    and blocks other modules' committed sessions. This file claims the 997_90x sub-range of the
    committed cross-session tg id range (see the db-integration reference).
    """
    async with db.begin() as session:
        user = User(first_name="Backfill Probe", tg_user_id=tg_user_id, settings=Settings())
        rows = build(user)
        session.add(user)
        session.add_all(rows)
        await session.flush()
        assert all(row.id is not None for row in rows)
        meetup_ids = [row.id for row in rows if row.id is not None]
    try:
        yield meetup_ids
    finally:
        async with db.begin() as session:
            for meetup_id in meetup_ids:
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("DELETE FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
                )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id = :tg").bindparams(tg=tg_user_id)
            )


async def run_backfill_committed():
    """Run the migration UPDATE in its own committed transaction.

    Committing also backfills any other module's committed rows that still carry a NULL
    title_tagged; the statement is escape-only on plain text, so that side effect is harmless —
    but assert nothing about rows this module did not seed.
    """
    async with db.begin() as session:
        await session.exec(text(BACKFILL_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657


async def load_meetup(meetup_id: int) -> Meetup:
    async with db.begin() as session:
        return (await session.exec(select(Meetup).where(Meetup.id == meetup_id))).one()


async def test_backfill_escapes_plain_columns_and_keeps_null_description(db_session: AsyncSession):
    def build(owner: User) -> list[Meetup]:
        return [make_meetup(NASTY_TEXT, owner, description=NASTY_TEXT), make_meetup("plain title", owner)]

    async with committed_probe_rows(997_900, build) as (escaped_id, no_description_id):
        await run_backfill_committed()

        escaped = await load_meetup(escaped_id)
        no_description = await load_meetup(no_description_id)

        # SQL replace-chain parity with Python's html.escape, which the model fallback and
        # serialize_entities use — the three escape paths must agree byte for byte.
        assert escaped.title_tagged == html.escape(NASTY_TEXT)
        assert escaped.description_tagged == html.escape(NASTY_TEXT)
        assert no_description.title_tagged == "plain title"
        assert no_description.description_tagged is None


async def test_backfill_skips_rows_that_already_carry_a_tagged_title(db_session: AsyncSession):
    def build(owner: User) -> list[Meetup]:
        already_tagged = make_meetup("plain", owner, description="plain too")
        already_tagged.title_tagged = "<b>already</b>"
        already_tagged.description_tagged = "kept"
        return [already_tagged]

    async with committed_probe_rows(997_901, build) as (meetup_id,):
        await run_backfill_committed()

        already_tagged = await load_meetup(meetup_id)
        assert already_tagged.title_tagged == "<b>already</b>"
        assert already_tagged.description_tagged == "kept"
