import html
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot.db
from mitup_bot.format_tags import strip_format_tags

pytestmark = pytest.mark.db_test

VERSIONS_DIR = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(mitup_bot.db.__file__).parent / "migrations" / "versions"
)


def load_migration_module(filename: str) -> ModuleType:
    """Load a revision module from its file location and return it, so the tests read the exact
    SQL constants the migration executes and break if those statements change.

    Revision modules can't be imported by dotted path — their names start with a digit.
    """
    spec = importlib.util.spec_from_file_location(f"migration_{filename.removesuffix('.py')}", VERSIONS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ESCAPE_SQL_BY_COLUMN: dict[str, str] = load_migration_module(
    "6d469cde828e_add_tagged_title_and_description_to_.py"
).ESCAPE_SQL_BY_COLUMN
STRIP_TAGS_SQL_BY_COLUMN: dict[str, str] = load_migration_module(
    "52824dd9ee6b_flip_tagged_titles_and_descriptions_.py"
).STRIP_TAGS_SQL_BY_COLUMN

NASTY_TEXT = """<b>hi</b> & "quotes" 'apostrophes' <3"""
CUSTOM_EMOJI_ID = "5368324170671202286"
TAGGED_TEXT = (
    "<b>&lt;b&gt;hi&lt;/b&gt;</b> &amp; &quot;quotes&quot; &#x27;apostrophes&#x27; "
    f'<tg-emoji emoji-id="{CUSTOM_EMOJI_ID}">😀</tg-emoji>'
)
PLAIN_TEXT = "<b>hi</b> & \"quotes\" 'apostrophes' 😀"


async def evaluate_expression(
    db_session: AsyncSession, expression: str, source_column: str, value: str | None
) -> str | None:
    """Run a migration SQL expression against a literal value bound to the column name it reads.

    The transitional tagged columns don't exist at the head schema, so the expressions are
    evaluated over a one-row subquery aliasing the bound value to the column each expression
    references. The expressions themselves stay live below head: a downgrade below the flip
    revision strips tagged text back into the original columns, and re-upgrading re-runs the
    escape backfill over the restored rows.
    """
    statement = text(f"SELECT {expression} FROM (SELECT CAST(:value AS TEXT) AS {source_column}) AS probe").bindparams(
        value=value
    )
    result = await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        statement
    )
    return result.scalar_one()


@pytest.mark.parametrize("column", ["title", "description"])
async def test_backfill_escape_chain_matches_html_escape(db_session: AsyncSession, column: str):
    # SQL replace-chain parity with Python's html.escape, which the model fallback and
    # serialize_entities use — the escape paths must agree byte for byte.
    escaped = await evaluate_expression(db_session, ESCAPE_SQL_BY_COLUMN[column], column, NASTY_TEXT)

    assert escaped == html.escape(NASTY_TEXT)


@pytest.mark.parametrize("column", ["title", "description"])
async def test_backfill_escape_chain_propagates_null(db_session: AsyncSession, column: str):
    # The backfill UPDATE has no per-column NULL gate — a NULL description must stay NULL
    # through the replace chain rather than turn into an empty string.
    assert await evaluate_expression(db_session, ESCAPE_SQL_BY_COLUMN[column], column, None) is None


@pytest.mark.parametrize("column", ["title", "description"])
async def test_flip_strip_chain_matches_strip_format_tags(db_session: AsyncSession, column: str):
    # SQL strip/unescape parity with the runtime strip helper — the two must agree so a
    # database downgraded below the flip revision shows exactly the text the plain accessors
    # were deriving.
    restored = await evaluate_expression(db_session, STRIP_TAGS_SQL_BY_COLUMN[column], f"{column}_tagged", TAGGED_TEXT)

    assert restored == PLAIN_TEXT
    assert restored == strip_format_tags(TAGGED_TEXT)
