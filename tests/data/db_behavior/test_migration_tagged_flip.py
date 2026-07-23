import contextlib
import importlib.util
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.format_tags import strip_format_tags
from mitup_bot.models import Meetup, Settings, User

pytestmark = pytest.mark.db_test

MIGRATION_PATH = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(db.__file__).parent / "migrations" / "versions" / "52824dd9ee6b_flip_tagged_titles_and_descriptions_.py"
)


def load_migration_sql() -> tuple[dict[str, str], dict[str, str]]:
    """Load the exact statements the migration runs, so this test breaks if they change.

    The revision module can't be imported by dotted path (its name starts with a digit), so load it
    from its file location and read the constants ``upgrade()`` / ``downgrade()`` execute.
    """
    spec = importlib.util.spec_from_file_location("_migration_52824dd9ee6b", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.COPY_TAGGED_SQL_BY_COLUMN, module.RESTORE_PLAIN_SQL_BY_COLUMN


COPY_SQL_BY_COLUMN, RESTORE_SQL_BY_COLUMN = load_migration_sql()

CUSTOM_EMOJI_ID = "5368324170671202286"
TAGGED_TEXT = (
    "<b>&lt;b&gt;hi&lt;/b&gt;</b> &amp; &quot;quotes&quot; &#x27;apostrophes&#x27; "
    f'<tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'
)
PLAIN_TEXT = "<b>hi</b> & \"quotes\" 'apostrophes' 😀"


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

    Every transaction in this module is short-lived and committed: the migration UPDATEs are
    whole-table, so they row-lock every meetup in the shared container whose tagged copy is
    non-NULL — executing them from a transaction that stays open (like the session-scoped
    ``db_session``) holds those locks and blocks other modules' committed sessions. This file
    claims the 997_91x sub-range of the committed cross-session tg id range (the backfill module
    holds 997_90x; see the db-integration reference).
    """
    async with db.begin() as session:
        user = User(first_name="Flip Probe", tg_user_id=tg_user_id, settings=Settings())
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


async def run_statements_committed(sql_by_column: dict[str, str]):
    """Run the migration UPDATEs in their own committed transaction.

    Committing also runs them against other modules' committed rows, but no other test module
    writes the ``*_tagged`` columns, so their tagged copies are NULL and the ``IS NOT NULL`` gate
    skips them. The exception is rows the backfill module's committed UPDATE has tagged with
    ``html.escape`` of their plain text: on those the COPY writes that escaped text into the plain
    column (the tagged form of the same visible text) and the RESTORE strips it back to the
    original, so both directions preserve meaning — still, assert nothing about rows this module
    did not seed.
    """
    async with db.begin() as session:
        for statement in sql_by_column.values():
            await session.exec(text(statement))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657


async def load_meetup(meetup_id: int) -> Meetup:
    async with db.begin() as session:
        return (await session.exec(select(Meetup).where(Meetup.id == meetup_id))).one()


async def test_upgrade_copies_tagged_values_and_keeps_null_description(db_session: AsyncSession):
    def build(owner: User) -> list[Meetup]:
        flipped = make_meetup("plain title", owner, description="plain description")
        flipped.title_tagged = TAGGED_TEXT
        flipped.description_tagged = TAGGED_TEXT
        no_description = make_meetup("plain title", owner)
        no_description.title_tagged = "just text"
        return [flipped, no_description]

    async with committed_probe_rows(997_910, build) as (flipped_id, no_description_id):
        await run_statements_committed(COPY_SQL_BY_COLUMN)

        flipped = await load_meetup(flipped_id)
        no_description = await load_meetup(no_description_id)

        assert flipped.title == TAGGED_TEXT
        assert flipped.description == TAGGED_TEXT
        assert no_description.title == "just text"
        assert no_description.description is None


async def test_upgrade_skips_rows_without_a_tagged_copy(db_session: AsyncSession):
    def build(owner: User) -> list[Meetup]:
        return [make_meetup("kept title", owner, description="kept description")]

    async with committed_probe_rows(997_911, build) as (untouched_id,):
        await run_statements_committed(COPY_SQL_BY_COLUMN)

        untouched = await load_meetup(untouched_id)
        assert untouched.title == "kept title"
        assert untouched.description == "kept description"


async def test_downgrade_restores_plain_text_from_the_tagged_columns(db_session: AsyncSession):
    def build(owner: User) -> list[Meetup]:
        restored = make_meetup(TAGGED_TEXT, owner, description=TAGGED_TEXT)
        restored.title_tagged = TAGGED_TEXT
        restored.description_tagged = TAGGED_TEXT
        no_description = make_meetup("just text", owner)
        no_description.title_tagged = "just text"
        return [restored, no_description]

    async with committed_probe_rows(997_912, build) as (restored_id, no_description_id):
        await run_statements_committed(RESTORE_SQL_BY_COLUMN)

        restored = await load_meetup(restored_id)
        no_description = await load_meetup(no_description_id)

        # SQL strip/unescape parity with the runtime strip helper — the two must agree so a
        # downgraded database shows exactly the text the plain accessors were deriving.
        assert restored.title == PLAIN_TEXT
        assert restored.title == strip_format_tags(TAGGED_TEXT)
        assert restored.description == PLAIN_TEXT
        assert no_description.title == "just text"
        assert no_description.description is None
