import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot.db

pytestmark = pytest.mark.db_test

VERSIONS_DIR = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(mitup_bot.db.__file__).parent / "migrations" / "versions"
)


def load_predicate() -> str:
    """The exact WHERE clause the acquisition backfill runs, read out of the revision module.

    Revision modules can't be imported by dotted path — their names start with a digit.
    """
    filename = "082a246a66e7_add_acquisition_source_to_users.py"
    spec = importlib.util.spec_from_file_location("migration_acquisition_source", VERSIONS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SHARED_CARD_ROWS_PREDICATE


SHARED_CARD_ROWS_PREDICATE = load_predicate()

# The 998_3xx throwaway range keeps these clear of the session-scoped seed users.
INLINE_JOINER = 998_301
PLACEHOLDER_INVITEE = -1
ALREADY_ATTRIBUTED = 998_302
MEMBER = 998_303

PROBE_ROWS_SQL = """
    INSERT INTO users (tg_user_id, first_name, status, acquisition_source) VALUES
        (:inline_joiner, 'backfill-probe', 'joined_only', NULL),
        (:placeholder, 'backfill-probe', 'joined_only', NULL),
        (:already, 'backfill-probe', 'joined_only', 'src_web'),
        (:member, 'backfill-probe', 'member', NULL)
"""


async def test_the_backfill_claims_inline_joiners_and_nothing_else(db_session: AsyncSession):
    """`joined_only` is the status the inline Join button assigns, so those rows can be attributed
    to the shared card after the fact. A placeholder invitee an owner typed in arrived from nowhere,
    a row that already names its source is not re-stated, and a member's silence stays honest —
    nothing observed where they came from.

    The predicate is evaluated instead of the UPDATE it belongs to: the statement matches on status
    alone, so running it inside the suite's long-lived transaction would lock every `users` row.
    """
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(PROBE_ROWS_SQL).bindparams(
                inline_joiner=INLINE_JOINER, placeholder=PLACEHOLDER_INVITEE, already=ALREADY_ATTRIBUTED, member=MEMBER
            )
        )

        claimed = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    f"SELECT tg_user_id FROM users WHERE {SHARED_CARD_ROWS_PREDICATE} AND first_name = 'backfill-probe'"
                )
            )
        ).all()

        assert [row.tg_user_id for row in claimed] == [INLINE_JOINER]
    finally:
        await savepoint.rollback()
