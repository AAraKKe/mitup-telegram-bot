"""Unit coverage of the pending-link row lifecycle against a mock session.

The atomicity and expiry rules are properties of PostgreSQL, so they are proven in
``tests/data/db_behavior/test_patreon_linking.py`` against a real database. What is covered here is
everything around them: what a staged row contains, what each statement filters on, and how a
transition's result is mapped.
"""

import datetime as dt

from freezegun import freeze_time

from mitup_bot.models import PatreonPendingLink
from mitup_bot.patreon import pairing
from mitup_bot.patreon.pending_links import (
    ClaimedLink,
    ClaimFailure,
    claim_pending_link,
    claim_statement,
    classification_statement,
    classify_claim_failure,
    consume_pending_link,
    consume_statement,
    spent_link_of,
    spent_link_statement,
    stage_pending_link,
)
from mitup_bot.supporter import SupporterLevel
from tests.helpers.stub_db import MockDbSession

# --- Staging: what the browser leg is allowed to write ---


async def test_staged_row_stores_the_hash_and_never_the_code():
    session = MockDbSession()

    code = await stage_pending_link(
        session, patreon_user_id="p-1", patreon_full_name="Ada L", supporter_level=SupporterLevel.HOST_2
    )

    staged = [obj for obj in session.objects_added if isinstance(obj, PatreonPendingLink)]
    assert len(staged) == 1
    assert staged[0].code_hash == pairing.hash_pairing_code(code)
    # The plaintext code exists only in the return value; nothing on the row can reproduce it.
    assert code not in str(staged[0].model_dump())


async def test_staged_row_carries_identity_and_tier_but_no_telegram_user():
    session = MockDbSession()

    await stage_pending_link(
        session, patreon_user_id="p-2", patreon_full_name="Ada L", supporter_level=SupporterLevel.HOST_3
    )

    staged = next(obj for obj in session.objects_added if isinstance(obj, PatreonPendingLink))
    assert staged.patreon_user_id == "p-2"
    assert staged.patreon_full_name == "Ada L"
    assert staged.supporter_level is SupporterLevel.HOST_3
    assert staged.consumed_time is None
    # The browser leg learns no Telegram account, so it cannot name one: the row leaves both the
    # claimer and the consumed marker for the Telegram half to fill in.
    assert staged.claimed_tg_user_id is None
    assert staged.claimed_time is None


async def test_staged_row_expires_after_the_pairing_ttl():
    session = MockDbSession()

    with freeze_time("2026-07-05 12:00:00"):
        await stage_pending_link(
            session, patreon_user_id="p-3", patreon_full_name=None, supporter_level=SupporterLevel.NONE
        )
        expected = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=pairing.PAIRING_CODE_TTL_SECONDS)

    staged = next(obj for obj in session.objects_added if isinstance(obj, PatreonPendingLink))
    assert staged.expiration == expected


async def test_each_staging_mints_a_distinct_code():
    session = MockDbSession()

    first = await stage_pending_link(
        session, patreon_user_id="p-4", patreon_full_name=None, supporter_level=SupporterLevel.NONE
    )
    second = await stage_pending_link(
        session, patreon_user_id="p-4", patreon_full_name=None, supporter_level=SupporterLevel.NONE
    )

    assert first != second


# --- Claiming and consuming ---


def test_claim_statement_binds_the_claimer_without_spending_the_row():
    sql = str(claim_statement("a-code", 4242).compile(compile_kwargs={"literal_binds": True}))

    assert "consumed_time IS NULL" in sql
    assert "expiration > now()" in sql
    # Claiming records who presented the code; only confirming spends the row.
    assert "SET claimed_tg_user_id=4242" in sql
    assert "consumed_time=now()" not in sql
    # A row already claimed by somebody else does not match, so a passed-on link cannot be re-pointed.
    assert "claimed_tg_user_id IS NULL OR" in sql
    # The lookup is by hash; the code itself never reaches the database.
    assert pairing.hash_pairing_code("a-code") in sql
    assert "a-code" not in sql.replace(pairing.hash_pairing_code("a-code"), "")


def test_consume_statement_requires_the_claimer_and_rechecks_expiry():
    sql = str(consume_statement("a-code", 4242).compile(compile_kwargs={"literal_binds": True}))

    assert "SET consumed_time=now()" in sql
    assert "consumed_time IS NULL" in sql
    # Re-checked at confirm time: a prompt opened before the deadline must not outlive it.
    assert "expiration > now()" in sql
    # The confirming account has to be the one that claimed the row, which is what makes a forged
    # confirm callback naming somebody else's pending link inert.
    assert "claimed_tg_user_id = 4242" in sql


def test_neither_transition_is_keyed_by_the_claimer_alone():
    # One person can legitimately hold several live rows, so a by-claimer lookup would have to pick
    # between them. Both statements address exactly one row, by its code hash.
    for sql in (
        str(claim_statement("a-code", 7).compile(compile_kwargs={"literal_binds": True})),
        str(consume_statement("a-code", 7).compile(compile_kwargs={"literal_binds": True})),
    ):
        assert sql.count("code_hash") == 1


async def test_claim_returns_the_identity_the_row_carried():
    session = MockDbSession()
    session.add_objects_with_statement(claim_statement("a-code", 7), (("p-5", "Ada L", SupporterLevel.HOST_1),))

    assert await claim_pending_link(session, "a-code", 7) == ClaimedLink("p-5", "Ada L", SupporterLevel.HOST_1)


async def test_claim_returns_none_when_nothing_matches():
    # Unknown, expired, already-spent and claimed-by-somebody-else all reach here as an empty result.
    assert await claim_pending_link(MockDbSession(), "no-such-code", 7) is None


async def test_consume_returns_the_granted_level_from_the_row():
    session = MockDbSession()
    session.add_objects_with_statement(consume_statement("a-code", 7), (("p-6", None, SupporterLevel.HOST_3),))

    confirmed = await consume_pending_link(session, "a-code", 7)

    assert confirmed == ClaimedLink("p-6", None, SupporterLevel.HOST_3)
    # The write reads its tier back out of the row, never from the prompt or the callback.
    assert confirmed is not None
    assert confirmed.granted_level is SupporterLevel.HOST_3


async def test_consume_returns_none_for_a_spent_or_unclaimed_row():
    # The routine "already used" branch: a double tap or a re-confirmed prompt lands here, and the
    # caller has to answer it rather than crash or silently do nothing.
    assert await consume_pending_link(MockDbSession(), "a-code", 7) is None


# --- Classifying a failed transition ---


def stored_row(*, claimed_tg_user_id: int | None = None, spent: bool = False) -> PatreonPendingLink:
    """A pending-link row as the classification and spent-link reads would return it."""
    return PatreonPendingLink(
        code_hash=pairing.hash_pairing_code("a-code"),
        patreon_user_id="p-9",
        patreon_full_name="Ada L",
        supporter_level=SupporterLevel.HOST_1,
        expiration=dt.datetime(2026, 7, 5, 12, 0),
        claimed_tg_user_id=claimed_tg_user_id,
        consumed_time=dt.datetime(2026, 7, 5, 12, 5) if spent else None,
    )


def classified(session: MockDbSession, row: PatreonPendingLink, *, expired: bool):
    """Register ``row`` (and its database-clock expiry verdict) under the classification read."""
    session.add_objects_with_statement(classification_statement("a-code"), ((row, expired),))


def test_classification_reads_expiry_from_the_database_clock():
    # The verdict must come from the same clock the transitions filter on, or the diagnosis could
    # disagree with the decision it explains.
    sql = str(classification_statement("a-code").compile(compile_kwargs={"literal_binds": True}))
    assert "expiration <= now()" in sql


async def test_an_unknown_code_classifies_as_unknown():
    assert await classify_claim_failure(MockDbSession(), "a-code", 7) is ClaimFailure.UNKNOWN_CODE


async def test_a_spent_row_classifies_as_consumed():
    session = MockDbSession()
    classified(session, stored_row(claimed_tg_user_id=7, spent=True), expired=False)

    assert await classify_claim_failure(session, "a-code", 7) is ClaimFailure.CONSUMED


async def test_a_row_claimed_by_somebody_else_classifies_as_claimed_by_other():
    session = MockDbSession()
    classified(session, stored_row(claimed_tg_user_id=8), expired=False)

    assert await classify_claim_failure(session, "a-code", 7) is ClaimFailure.CLAIMED_BY_OTHER


async def test_an_expired_row_classifies_as_expired():
    session = MockDbSession()
    classified(session, stored_row(claimed_tg_user_id=7), expired=True)

    assert await classify_claim_failure(session, "a-code", 7) is ClaimFailure.EXPIRED


async def test_a_live_unclaimed_row_classifies_as_unclaimed_not_expired():
    # A live unclaimed row always matches a claim, and the confirm button only exists after a claim
    # succeeded — so this shape reaching classification means a consume was forged around the claim
    # step. Its label must stay out of EXPIRED, whose honest baseline would swallow the signal.
    session = MockDbSession()
    classified(session, stored_row(claimed_tg_user_id=None), expired=False)

    assert await classify_claim_failure(session, "a-code", 7) is ClaimFailure.UNCLAIMED


# --- Reading back a spent link ---


def test_spent_link_statement_requires_the_spender_and_the_spent_marker():
    sql = str(spent_link_statement("a-code", 4242).compile(compile_kwargs={"literal_binds": True}))

    assert "consumed_time IS NOT NULL" in sql
    assert "claimed_tg_user_id = 4242" in sql
    assert pairing.hash_pairing_code("a-code") in sql


async def test_spent_link_of_returns_the_identity_for_the_account_that_spent_it():
    session = MockDbSession()
    session.add_objects_with_statement(
        spent_link_statement("a-code", 7), (stored_row(claimed_tg_user_id=7, spent=True),)
    )

    assert await spent_link_of(session, "a-code", 7) == ClaimedLink("p-9", "Ada L", SupporterLevel.HOST_1)


async def test_spent_link_of_returns_none_when_nothing_matches():
    assert await spent_link_of(MockDbSession(), "a-code", 7) is None
