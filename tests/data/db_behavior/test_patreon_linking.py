"""Real-Postgres proof of Patreon linking: pending links and ``link_patreon_account``.

The single-use and expiry guarantees of a pairing code are properties of one conditional UPDATE
evaluated by PostgreSQL, so they cannot be shown against a stub session. Neither can the concurrency
case, which is the one that matters: two taps racing must not both link.

These paths open their own transactions and commit, so the seeds here must be committed too (the
session fixture's uncommitted rows are invisible to them). Committed cross-session data uses the 997
range; this file claims the 997_6xx sub-range.
"""

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import ApplicationHandlerStop, ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.handlers.collaborate.patreon_redemption import claim_update
from mitup_bot.models import PatreonPendingLink, Settings, SupporterSubscription, User, configure_token_encryption
from mitup_bot.models.subscriptions import TokenCipher
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import pairing, pending_links
from mitup_bot.patreon.pending_links import ClaimedLink, claim_pending_link, consume_pending_link
from mitup_bot.patreon_link import LinkOutcome, link_patreon_account
from mitup_bot.supporter import SupporterLevel

pytestmark = pytest.mark.db_test

PATRON_USER_ID = "patreon-6001"
NON_PATRON_USER_ID = "patreon-6002"


@pytest.fixture(autouse=True)
def configured_db(db_session: AsyncSession) -> AsyncSession:
    """Depend on the session-scoped ``db_session`` so ``configure_db`` has run before these tests
    open their own ``db.begin`` transactions."""
    return db_session


@pytest.fixture(autouse=True, scope="module")
def configured_token_encryption() -> Iterator[None]:
    saved = TokenCipher.cipher
    configure_token_encryption(Fernet.generate_key().decode())
    try:
        yield
    finally:
        TokenCipher.cipher = saved


class RecordingBot:
    """Captures the confirmation DM the write lifecycle drains after commit."""

    def __init__(self):
        self.sent: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object):
        self.sent.append(kwargs)


def make_api(bot: RecordingBot) -> TelegramApi:
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, bot), MetricsClient(NullBackend()))
    return api


@contextlib.asynccontextmanager
async def committed_user(tg_user_id: int) -> AsyncIterator[int]:
    async with db.begin() as session:
        user = User(first_name="Linking User", tg_user_id=tg_user_id, settings=Settings())
        session.add(user)
        await session.flush()
        user_id = user.db_id
    try:
        yield user_id
    finally:
        # supporter_subscriptions has ON DELETE CASCADE, so removing the user clears its row too.
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id = :t").bindparams(t=tg_user_id)
            )


@contextlib.asynccontextmanager
async def committed_pending_link(
    patreon_user_id: str,
    level: SupporterLevel = SupporterLevel.HOST_2,
    *,
    expires_in: dt.timedelta = dt.timedelta(minutes=15),
) -> AsyncIterator[str]:
    """Stage a pending link exactly as the OAuth callback would, and yield its pairing code."""
    code = pairing.generate_pairing_code()
    code_hash = pairing.hash_pairing_code(code)
    async with db.begin() as session:
        session.add(
            PatreonPendingLink(
                code_hash=code_hash,
                patreon_user_id=patreon_user_id,
                patreon_full_name="Ada Lovelace",
                supporter_level=level,
                expiration=dt.datetime.now(dt.UTC) + expires_in,
            )
        )
    try:
        yield code
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM patreon_pending_links WHERE code_hash = :h").bindparams(h=code_hash)
            )


async def claim_in_own_transaction(code: str, tg_user_id: int = 555_001) -> ClaimedLink | None:
    """One short committed transaction per transition, as the handlers do."""
    async with db.begin() as session:
        return await claim_pending_link(session, code, tg_user_id)


async def consume_in_own_transaction(code: str, tg_user_id: int = 555_001) -> ClaimedLink | None:
    async with db.begin() as session:
        return await consume_pending_link(session, code, tg_user_id)


async def classify_in_own_transaction(code: str, tg_user_id: int) -> pending_links.ClaimFailure:
    async with db.begin() as session:
        return await pending_links.classify_claim_failure(session, code, tg_user_id)


async def read_user_and_subscription(user_id: int) -> tuple[User, SupporterSubscription | None]:
    async with db.begin() as session:
        user = (await session.exec(select(User).where(User.id == user_id))).one()
        subscription = (
            await session.exec(select(SupporterSubscription).where(SupporterSubscription.user_id == user_id))
        ).first()
        return user, subscription


async def read_pending_link(code: str) -> PatreonPendingLink | None:
    async with db.begin() as session:
        return (
            await session.exec(
                select(PatreonPendingLink).where(PatreonPendingLink.code_hash == pairing.hash_pairing_code(code))
            )
        ).first()


# --- Pending links: two transitions, single use, expiry, and the races ---


async def test_claiming_binds_the_row_without_spending_it():
    async with committed_pending_link("patreon-6100", SupporterLevel.HOST_3) as code:
        claimed = await claim_in_own_transaction(code, 555_100)

        assert claimed == ClaimedLink("patreon-6100", "Ada Lovelace", SupporterLevel.HOST_3)
        row = await read_pending_link(code)
        assert row is not None
        assert row.claimed_tg_user_id == 555_100
        # Claiming only opens the prompt; the row stays spendable until someone confirms.
        assert row.consumed_time is None


async def test_the_same_account_may_reclaim_its_own_link():
    # An honest second tap on the deep link must re-open the prompt rather than fail.
    async with committed_pending_link("patreon-6101") as code:
        assert await claim_in_own_transaction(code, 555_101) is not None
        assert await claim_in_own_transaction(code, 555_101) is not None


async def test_a_link_claimed_by_one_account_cannot_be_claimed_by_another():
    # A finish link passed on after it was already tapped cannot be re-pointed at the new holder.
    async with committed_pending_link("patreon-6102") as code:
        assert await claim_in_own_transaction(code, 555_102) is not None
        assert await claim_in_own_transaction(code, 555_999) is None


async def test_confirming_spends_the_row():
    async with committed_pending_link("patreon-6103", SupporterLevel.HOST_1) as code:
        await claim_in_own_transaction(code, 555_103)

        confirmed = await consume_in_own_transaction(code, 555_103)

        assert confirmed == ClaimedLink("patreon-6103", "Ada Lovelace", SupporterLevel.HOST_1)
        spent = await read_pending_link(code)
        assert spent is not None
        assert spent.consumed_time is not None


async def test_a_link_can_only_be_confirmed_once():
    async with committed_pending_link("patreon-6104") as code:
        await claim_in_own_transaction(code, 555_104)
        assert await consume_in_own_transaction(code, 555_104) is not None
        # A double tap, a back button, or a re-confirmed prompt all land here.
        assert await consume_in_own_transaction(code, 555_104) is None


async def test_only_the_claiming_account_can_confirm():
    # The database half of the forged-confirm defence: holding the code is not enough.
    async with committed_pending_link("patreon-6105") as code:
        await claim_in_own_transaction(code, 555_105)

        assert await consume_in_own_transaction(code, 555_999) is None
        unspent = await read_pending_link(code)
        assert unspent is not None
        assert unspent.consumed_time is None


async def test_an_unclaimed_link_cannot_be_confirmed():
    # Skipping straight to a confirm, without the `/start` that claims the row, matches nothing.
    async with committed_pending_link("patreon-6106") as code:
        assert await consume_in_own_transaction(code, 555_106) is None


async def test_two_simultaneous_confirmations_yield_exactly_one_winner():
    # Two taps on the same prompt (a double tap, or two devices) must not both link. The consume is
    # one conditional UPDATE, so PostgreSQL serializes them on the row and the loser re-evaluates
    # the not-yet-consumed filter against the winner's commit.
    async with committed_pending_link("patreon-6107") as code:
        await claim_in_own_transaction(code, 555_107)

        results = await asyncio.gather(
            consume_in_own_transaction(code, 555_107), consume_in_own_transaction(code, 555_107)
        )

        assert sum(result is not None for result in results) == 1


async def test_an_expired_code_cannot_be_claimed():
    async with committed_pending_link("patreon-6108", expires_in=dt.timedelta(seconds=-1)) as code:
        assert await claim_in_own_transaction(code) is None
        # The row is left untouched rather than silently claimed.
        unclaimed = await read_pending_link(code)
        assert unclaimed is not None
        assert unclaimed.claimed_tg_user_id is None


async def test_expiry_is_re_checked_at_confirm_time():
    # A prompt opened just before the deadline must not still be confirmable after it. Expiry lives
    # in the consume predicate for exactly this reason, not only in the claim.
    async with committed_pending_link("patreon-6109", expires_in=dt.timedelta(seconds=2)) as code:
        assert await claim_in_own_transaction(code, 555_109) is not None
        await asyncio.sleep(2.1)

        assert await consume_in_own_transaction(code, 555_109) is None
        unspent = await read_pending_link(code)
        assert unspent is not None
        assert unspent.consumed_time is None


async def test_claim_and_consume_handle_a_real_sized_telegram_id():
    # Telegram ids run past 2^31 and `claimed_tg_user_id` is a BigInteger. A narrower bind would not
    # raise here -- it would match zero rows, which the flow renders as the same "link no longer
    # works" message as an expired code, so a user with a high id would be permanently and silently
    # unable to link. Small fixture ids pass either way, which is exactly why this one is large.
    big_tg_user_id = 7_000_000_000
    async with committed_pending_link("patreon-6113", SupporterLevel.HOST_2) as code:
        assert await claim_in_own_transaction(code, big_tg_user_id) is not None

        row = await read_pending_link(code)
        assert row is not None
        assert row.claimed_tg_user_id == big_tg_user_id
        # And the id round-trips through the consume predicate too, not just the claim's SET.
        assert await consume_in_own_transaction(code, big_tg_user_id) is not None


async def test_classification_tells_a_forged_confirm_from_an_expired_code():
    # The two shapes behind the same failed consume, split by PostgreSQL's clock — the clock the
    # transitions themselves filter on. A live row nobody claimed means the confirm was forged
    # around the claim step (near-zero baseline, alarmable); an expired row is the honest rate.
    async with committed_pending_link("patreon-6114") as code:
        assert await consume_in_own_transaction(code, 555_114) is None
        assert await classify_in_own_transaction(code, 555_114) is pending_links.ClaimFailure.UNCLAIMED

    async with committed_pending_link("patreon-6115", expires_in=dt.timedelta(seconds=-1)) as code:
        assert await consume_in_own_transaction(code, 555_115) is None
        assert await classify_in_own_transaction(code, 555_115) is pending_links.ClaimFailure.EXPIRED


async def test_a_spent_link_is_readable_only_by_its_spender():
    # What the double-tap answer stands on: the losing tap can read back the identity its own
    # winning tap spent, and nobody else can.
    async with committed_pending_link("patreon-6116", SupporterLevel.HOST_1) as code:
        await claim_in_own_transaction(code, 555_116)
        assert await consume_in_own_transaction(code, 555_116) is not None

        async with db.begin() as session:
            assert await pending_links.spent_link_of(session, code, 555_116) == ClaimedLink(
                "patreon-6116", "Ada Lovelace", SupporterLevel.HOST_1
            )
            assert await pending_links.spent_link_of(session, code, 555_999) is None


async def test_an_unknown_code_claims_nothing():
    assert await claim_in_own_transaction(pairing.generate_pairing_code()) is None


async def test_the_stored_row_never_contains_the_code():
    async with committed_pending_link("patreon-6110") as code:
        stored = await read_pending_link(code)

        assert stored is not None
        assert stored.code_hash != code
        assert stored.code_hash == pairing.hash_pairing_code(code)


async def test_one_account_may_hold_several_live_links():
    # Starting the flow twice is a valid state. Nothing is keyed by the claimer, so both rows stay
    # independently claimable and confirmable.
    async with (
        committed_pending_link("patreon-6111") as first,
        committed_pending_link("patreon-6112") as second,
    ):
        assert await claim_in_own_transaction(first, 555_111) is not None
        assert await claim_in_own_transaction(second, 555_111) is not None

        assert await consume_in_own_transaction(first, 555_111) is not None
        untouched = await read_pending_link(second)
        assert untouched is not None
        assert untouched.consumed_time is None


async def test_the_claim_survives_the_stop_that_ends_the_handler():
    """``claim_update`` wraps OUTSIDE ``with_session``, so the claim commits before
    ``ApplicationHandlerStop`` unwinds. Nested the other way around the raise would happen inside
    the session scope and roll the bind back, and the damage would be quiet: the user would be
    shown a prompt against an unbound row, and confirming would then either fail its
    ``claimed_tg_user_id`` check or re-bind the row to whoever pressed it.

    The handler-level tests run on a stub session through ``call_handler``, so they never exercise
    the real context manager and would not notice the decorators being swapped. This drives the
    claim through the actual stack and reads the row back from a fresh session.
    """
    async with committed_pending_link("patreon-6150") as code:

        @claim_update
        @db.with_session
        async def probe(session: AsyncSession) -> None:
            await pending_links.claim_pending_link(session, code, 555_150)

        with pytest.raises(ApplicationHandlerStop):
            await probe()

        bound = await read_pending_link(code)
        assert bound is not None
        assert bound.claimed_tg_user_id == 555_150


# --- Retention: the sweep is the only exit these rows have ---


async def test_the_sweep_erases_spent_and_expired_rows_but_not_live_ones():
    async with (
        committed_pending_link("patreon-6140") as spent,
        committed_pending_link("patreon-6141", expires_in=dt.timedelta(seconds=-1)) as expired,
        committed_pending_link("patreon-6142") as live,
    ):
        await claim_in_own_transaction(spent, 555_140)
        await consume_in_own_transaction(spent, 555_140)

        async with db.begin() as session:
            deleted = await pending_links.delete_finished_pending_links(session)

        assert deleted >= 2
        assert await read_pending_link(spent) is None
        assert await read_pending_link(expired) is None
        # A row still inside its window is untouched: expiry is enforced by the predicate, and the
        # sweep must not shorten it.
        assert await read_pending_link(live) is not None


async def test_a_deletion_request_erases_that_users_claimed_rows():
    # patreon_pending_links has no foreign key to users, so a purge cascades nothing into it. Once a
    # row is claimed it pairs a Telegram id with a Patreon id, which a deletion request has to reach.
    async with committed_pending_link("patreon-6143") as claimed, committed_pending_link("patreon-6144") as other:
        await claim_in_own_transaction(claimed, 555_143)
        await claim_in_own_transaction(other, 555_199)

        async with db.begin() as session:
            deleted = await pending_links.delete_pending_links_for_users(session, [555_143])

        assert deleted == 1
        assert await read_pending_link(claimed) is None
        assert await read_pending_link(other) is not None


async def test_purging_users_leaves_unclaimed_rows_to_the_sweep():
    # An unclaimed row carries no Telegram identity by construction, so it is not addressable by a
    # deletion request. That is the case with no other exit at all.
    async with committed_pending_link("patreon-6145") as unclaimed:
        async with db.begin() as session:
            assert await pending_links.delete_pending_links_for_users(session, [555_145]) == 0

        assert await read_pending_link(unclaimed) is not None


async def test_purging_no_users_touches_nothing():
    async with committed_pending_link("patreon-6146") as code:
        await claim_in_own_transaction(code, 555_146)

        async with db.begin() as session:
            assert await pending_links.delete_pending_links_for_users(session, []) == 0

        assert await read_pending_link(code) is not None


# --- End to end: the consent leg cannot choose the account it lands on ---


async def test_a_staged_identity_lands_on_whoever_confirms_it():
    # The attack this flow closes: the browser leg stages an identity with no say in whose Telegram
    # account it attaches to. The account that confirms is the account it binds to.
    async with committed_user(997_640) as redeemer_id, committed_pending_link("patreon-6120") as code:
        await claim_in_own_transaction(code, 555_120)
        confirmed = await consume_in_own_transaction(code, 555_120)
        assert confirmed is not None

        outcome, _ = await link_for(redeemer_id, confirmed.patreon_user_id, confirmed.granted_level)

        assert outcome is LinkOutcome.LINKED_SUPPORTER
        user, subscription = await read_user_and_subscription(redeemer_id)
        assert subscription is not None
        assert subscription.user_id == redeemer_id
        assert subscription.patreon_user_id == "patreon-6120"
        assert user.supporter_level is SupporterLevel.HOST_2


async def test_a_second_account_cannot_reuse_a_spent_code():
    # Continuation of the same scenario: once the code has been confirmed nobody else can present
    # it, so a leaked code grants a second account nothing.
    async with committed_user(997_641) as second_user_id, committed_pending_link("patreon-6121") as code:
        await claim_in_own_transaction(code, 555_121)
        assert await consume_in_own_transaction(code, 555_121) is not None
        assert await consume_in_own_transaction(code, 555_121) is None

        _, subscription = await read_user_and_subscription(second_user_id)
        assert subscription is None


async def test_a_concurrent_link_of_the_same_patreon_account_loses_gracefully():
    # One person can run the consent leg twice for the same Patreon account and hand out two codes,
    # which makes this race ordinary rather than theoretical. Run genuinely concurrently, both
    # transactions read before either commits, so both pass the read-side check and the loser hits
    # the unique index. That must be the already-linked outcome, not an unhandled exception.
    async with committed_user(997_642) as first_user_id, committed_user(997_643) as second_user_id:
        outcomes = await asyncio.gather(
            link_for(first_user_id, "patreon-shared-664", SupporterLevel.HOST_2),
            link_for(second_user_id, "patreon-shared-664", SupporterLevel.HOST_2),
        )

        results = sorted(outcome.name for outcome, _ in outcomes)
        assert results == [LinkOutcome.ALREADY_LINKED_ELSEWHERE.name, LinkOutcome.LINKED_SUPPORTER.name]
        # Exactly one of them ended up with the subscription.
        subscriptions = [
            subscription
            for _, subscription in [
                await read_user_and_subscription(first_user_id),
                await read_user_and_subscription(second_user_id),
            ]
            if subscription is not None
        ]
        assert len(subscriptions) == 1


async def test_a_user_pending_deletion_gains_nothing():
    async with committed_user(997_644) as user_id:
        async with db.begin() as session:
            user = (await session.exec(select(User).where(User.id == user_id))).one()
            user.status = UserStatus.DELETION_REQUESTED

        outcome, bot = await link_for(user_id, "patreon-6130", SupporterLevel.HOST_2)

        assert outcome is LinkOutcome.PENDING_DELETION
        user, subscription = await read_user_and_subscription(user_id)
        assert subscription is None
        assert user.supporter_level is SupporterLevel.NONE
        assert bot.sent == []


# --- Persisting the link ---


async def link_for(user_id: int, patreon_user_id: str, level: SupporterLevel) -> tuple[LinkOutcome, RecordingBot]:
    """Run one redemption's write exactly as the handler does: the caller's write-mode session, the
    user loaded from the incoming update, and the fan-out draining after commit."""
    bot = RecordingBot()
    api = make_api(bot)
    async with db.begin_write(api) as session:
        user = (await session.exec(select(User).where(User.id == user_id))).one()
        outcome = await link_patreon_account(session, api, user, patreon_user_id=patreon_user_id, granted_level=level)
    return outcome, bot


async def test_new_link_for_patron_grants_support():
    async with committed_user(997_600) as user_id:
        outcome, bot = await link_for(user_id, PATRON_USER_ID, SupporterLevel.HOST_2)

        assert outcome is LinkOutcome.LINKED_SUPPORTER
        user, subscription = await read_user_and_subscription(user_id)
        assert user.supporter_level is SupporterLevel.HOST_2
        assert subscription is not None
        assert subscription.patreon_user_id == PATRON_USER_ID
        assert subscription.support_expiration is not None
        assert len(bot.sent) == 1


async def test_new_link_for_non_patron_stores_without_support():
    async with committed_user(997_610) as user_id:
        outcome, bot = await link_for(user_id, NON_PATRON_USER_ID, SupporterLevel.NONE)

        assert outcome is LinkOutcome.LINKED_NO_PATRON
        user, subscription = await read_user_and_subscription(user_id)
        assert user.supporter_level is SupporterLevel.NONE
        assert subscription is not None
        assert subscription.support_expiration is None
        assert len(bot.sent) == 1


async def test_relink_updates_subscription_in_place():
    async with committed_user(997_620) as user_id:
        async with db.begin() as session:
            session.add(SupporterSubscription(user_id=user_id, patreon_user_id=PATRON_USER_ID))
            await session.flush()
            original_id = (
                (await session.exec(select(SupporterSubscription).where(SupporterSubscription.user_id == user_id)))
                .one()
                .db_id
            )

        outcome, _ = await link_for(user_id, PATRON_USER_ID, SupporterLevel.HOST_2)

        assert outcome is LinkOutcome.LINKED_SUPPORTER
        user, subscription = await read_user_and_subscription(user_id)
        assert subscription is not None
        assert subscription.db_id == original_id  # updated in place, not recreated
        assert user.supporter_level is SupporterLevel.HOST_2


async def test_patreon_account_already_linked_to_another_user_is_rejected():
    async with committed_user(997_630) as first_user_id, committed_user(997_631) as second_user_id:
        async with db.begin() as session:
            session.add(SupporterSubscription(user_id=first_user_id, patreon_user_id="patreon-shared-663"))
            await session.flush()

        outcome, bot = await link_for(second_user_id, "patreon-shared-663", SupporterLevel.HOST_2)

        assert outcome is LinkOutcome.ALREADY_LINKED_ELSEWHERE
        second_user, second_subscription = await read_user_and_subscription(second_user_id)
        assert second_subscription is None
        assert second_user.supporter_level is SupporterLevel.NONE
        assert bot.sent == []
