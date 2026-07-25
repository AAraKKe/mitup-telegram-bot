"""The lifecycle of a ``patreon_pending_links`` row: staged, claimed, consumed, erased.

A pending link is what the OAuth callback produces instead of a subscription. It records the Patreon
identity the consent proved and nothing about who will claim it, because a browser cannot prove that.
Two conditional updates move it forward — a claim that binds it to a Telegram account, and a consume
that spends it once that account confirms — and each re-checks expiry against the database clock, so
neither decision can be outrun by a stale read.

This lives beside the OAuth client rather than in the bot app because both the bot (which creates and
redeems these rows) and the recurrent-events runner (which is the only thing that ever erases them)
need it, and neither app may import the other.
"""

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum, auto

from sqlalchemy import ColumnElement, Row, func, update
from sqlalchemy.sql.dml import ReturningUpdate
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import Select, SelectOfScalar

from mitup_bot.models import PatreonPendingLink
from mitup_bot.patreon import pairing
from mitup_bot.supporter import SupporterLevel


@dataclass(frozen=True)
class ClaimedLink:
    """What a claimed pending link carried: the Patreon identity, its display name, and the tier it
    was entitled to at consent time.

    ``granted_level`` is what the redemption would grant. It is deliberately not named after the
    user's current level: the prompt shows both numbers and confusing them is how a display value
    turns into a write value. This one is authoritative for the write and may be slightly stale;
    what the user holds today is read live from ``User.supporter_level`` at render time and is
    informational only.
    """

    patreon_user_id: str
    patreon_full_name: str | None
    granted_level: SupporterLevel


async def stage_pending_link(
    session: AsyncSession, *, patreon_user_id: str, patreon_full_name: str | None, supporter_level: SupporterLevel
) -> str:
    """Park a consented Patreon identity and return the pairing code that claims it.

    Only the code's hash is persisted, so the returned value exists nowhere else — it is rendered to
    the browser that completed the consent and never travels through the database.
    """
    code = pairing.generate_pairing_code()
    session.add(
        PatreonPendingLink(
            code_hash=pairing.hash_pairing_code(code),
            patreon_user_id=patreon_user_id,
            patreon_full_name=patreon_full_name,
            supporter_level=supporter_level,
            expiration=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=pairing.PAIRING_CODE_TTL_SECONDS),
        )
    )
    return code


def live_row_predicate(code: str) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[bool]]:
    """The filter both transitions share: this code's row, not yet spent, not yet expired.

    Expiry lives here rather than in a separate read or in a cleanup job on purpose. A check outside
    the statement can go stale between reading and writing, and a sweep that has not run yet would
    leave an expired row redeemable — the predicate is the only place that cannot be outrun. It uses
    the database clock, so the decision never depends on how far an application host has drifted.
    """
    return (
        col(PatreonPendingLink.code_hash) == pairing.hash_pairing_code(code),
        col(PatreonPendingLink.consumed_time).is_(None),
        col(PatreonPendingLink.expiration) > func.now(),
    )


def claim_statement(code: str, tg_user_id: int) -> ReturningUpdate[tuple[str, str | None, SupporterLevel]]:
    """The conditional UPDATE that binds a pending link to the account presenting the code.

    Claiming records an owner without spending the row, so the confirm step has something to check
    the confirming account against. A row already claimed by somebody else does not match, which is
    what stops a passed-on finish link from being re-pointed; the same account re-tapping its own
    link does match, so an honest second tap re-opens the prompt instead of failing.

    Keyed on the code hash alone. Nothing looks a row up by its claimer: one person can hold several
    live rows, so a by-claimer lookup would have to pick between them arbitrarily.

    ``claimed_tg_user_id`` is a BigInteger because real Telegram ids run past 2^31, and a narrower
    bind would be uniquely hard to spot here: a conditional UPDATE that mis-binds does not fail
    loudly, it matches zero rows, which this flow deliberately renders as the same friendly "link no
    longer works" message as an expired code. The db-gated tests therefore claim with an id above
    2^31 rather than a small fixture value.
    """
    return (
        update(PatreonPendingLink)
        .where(
            *live_row_predicate(code),
            (col(PatreonPendingLink.claimed_tg_user_id).is_(None))
            | (col(PatreonPendingLink.claimed_tg_user_id) == tg_user_id),
        )
        .values(claimed_tg_user_id=tg_user_id, claimed_time=func.now())
        .returning(
            col(PatreonPendingLink.patreon_user_id),
            col(PatreonPendingLink.patreon_full_name),
            col(PatreonPendingLink.supporter_level),
        )
    )


def consume_statement(code: str, tg_user_id: int) -> ReturningUpdate[tuple[str, str | None, SupporterLevel]]:
    """The conditional UPDATE that spends a pending link once its claimer confirms.

    Expiry is re-checked here, not just at claim time: a prompt opened seconds before the deadline
    must not still be confirmable an hour later. The claimer match is what makes a forged confirm
    callback inert — holding the code is not enough, the confirming account has to be the one that
    claimed the row.

    The granted tier is read back out of the row rather than taken from the callback or from
    anything rendered into the prompt, so the value written can never be one a client supplied.
    """
    return (
        update(PatreonPendingLink)
        .where(*live_row_predicate(code), col(PatreonPendingLink.claimed_tg_user_id) == tg_user_id)
        .values(consumed_time=func.now())
        .returning(
            col(PatreonPendingLink.patreon_user_id),
            col(PatreonPendingLink.patreon_full_name),
            col(PatreonPendingLink.supporter_level),
        )
    )


def to_claimed_link(
    row: Row[tuple[str, str | None, SupporterLevel]] | tuple[str, str | None, SupporterLevel] | None,
) -> ClaimedLink | None:
    """Map a transition's RETURNING row onto a ClaimedLink, or None when nothing matched."""
    if row is None:
        return None
    patreon_user_id, patreon_full_name, granted_level = row
    return ClaimedLink(
        patreon_user_id=patreon_user_id, patreon_full_name=patreon_full_name, granted_level=granted_level
    )


async def claim_pending_link(session: AsyncSession, code: str, tg_user_id: int) -> ClaimedLink | None:
    """Bind the pending link matching ``code`` to ``tg_user_id``, or return None when it cannot be.

    Unknown, expired, already-spent and claimed-by-somebody-else are one answer — None — on purpose:
    telling them apart would confirm to a guesser that a code exists.
    """
    return to_claimed_link((await session.exec(claim_statement(code, tg_user_id))).first())


class ClaimFailure(Enum):
    """Why a claim or consume matched no row. Diagnostic only, and deliberately never user-facing."""

    UNKNOWN_CODE = auto()
    EXPIRED = auto()
    CONSUMED = auto()
    CLAIMED_BY_OTHER = auto()
    UNCLAIMED = auto()


def classification_statement(code: str) -> Select[tuple[PatreonPendingLink, bool]]:
    """The read that backs failure classification: the row plus whether it has expired.

    Expiry is judged by the database clock — the clock both transitions filter on — so the diagnosis
    can never disagree with the decision it explains because an application host has drifted.
    """
    return select(PatreonPendingLink, (col(PatreonPendingLink.expiration) <= func.now()).label("expired")).where(
        col(PatreonPendingLink.code_hash) == pairing.hash_pairing_code(code)
    )


async def classify_claim_failure(session: AsyncSession, code: str, tg_user_id: int) -> ClaimFailure:
    """Work out why a transition matched nothing, for metrics and logs.

    The user-facing answer stays the same whichever value this returns — see ``claim_pending_link``
    for why that matters. This exists because the *operator* needs the distinction: without it a bug
    in the claim path is indistinguishable from an ordinary expired code, and would present as a
    small permanent trickle of people told their link no longer works, with nothing to alert on.

    ``UNCLAIMED`` — a live row nobody has claimed — deserves its own label because it can never
    come out of an honest tap: a live unclaimed row always matches a claim, and the confirm button
    only exists after a claim succeeded, so an UNCLAIMED consume is a forged callback carrying a
    code that skipped the claim step. Folding it into EXPIRED would hide the one near-zero-baseline
    signal of that forgery inside the label with an honest rate.

    Reads only, and only ever after the transition has already failed, so it cannot influence the
    write.
    """
    result = (await session.exec(classification_statement(code))).first()
    if result is None:
        return ClaimFailure.UNKNOWN_CODE
    row, expired = result
    if row.consumed_time is not None:
        return ClaimFailure.CONSUMED
    if row.claimed_tg_user_id is not None and row.claimed_tg_user_id != tg_user_id:
        return ClaimFailure.CLAIMED_BY_OTHER
    if expired:
        return ClaimFailure.EXPIRED
    return ClaimFailure.UNCLAIMED


async def consume_pending_link(session: AsyncSession, code: str, tg_user_id: int) -> ClaimedLink | None:
    """Spend the pending link matching ``code`` on behalf of ``tg_user_id``, or return None.

    None is a routine answer, not an edge case: a double-tapped prompt, a back button, or a prompt
    confirmed twice all land here on the second pass, and the caller has to tell the user the link
    is spent rather than crash or silently do nothing.

    A single statement is what makes a code usable exactly once. Two simultaneous confirmations
    serialize on the row: the second re-evaluates the ``consumed_time IS NULL`` filter against the
    committed update, matches nothing, and gets None.
    """
    return to_claimed_link((await session.exec(consume_statement(code, tg_user_id))).first())


def spent_link_statement(code: str, tg_user_id: int) -> SelectOfScalar[PatreonPendingLink]:
    """The row for ``code`` only if ``tg_user_id`` already claimed and spent it."""
    return select(PatreonPendingLink).where(
        col(PatreonPendingLink.code_hash) == pairing.hash_pairing_code(code),
        col(PatreonPendingLink.consumed_time).is_not(None),
        col(PatreonPendingLink.claimed_tg_user_id) == tg_user_id,
    )


async def spent_link_of(session: AsyncSession, code: str, tg_user_id: int) -> ClaimedLink | None:
    """The identity a spent code carried, when ``tg_user_id`` is the account that spent it.

    This is how a double tap on the confirm button gets an honest answer: the losing tap's consume
    matches nothing, but when this same account already spent this exact code and holds the link it
    wrote, the truthful screen is the linked one — not a message claiming nothing changed.
    """
    row = (await session.exec(spent_link_statement(code, tg_user_id))).first()
    if row is None:
        return None
    return ClaimedLink(
        patreon_user_id=row.patreon_user_id,
        patreon_full_name=row.patreon_full_name,
        granted_level=row.supporter_level,
    )


async def delete_finished_pending_links(session: AsyncSession) -> int:
    """Delete every pending link that can no longer be used, and report how many went.

    This is the **only** way an unclaimed row is ever erased, which makes it a retention mechanism
    rather than housekeeping. ``patreon_pending_links`` deliberately has no foreign key to ``users``
    — the callback cannot know whose Telegram account consented, and inventing that association
    would rebuild the flaw this whole flow exists to close — so nothing cascades into it. A user
    purge deletes ``User`` rows and relies on cascade; unlinking deletes the subscription. Neither
    reaches here.

    That leaves one case with no other exit at all: somebody completes the Patreon consent, never
    taps the finish link, and has no account to attach the row to and no button to press. Their
    ``patreon_user_id`` goes away when this runs and at no other time.
    """
    result = await session.exec(
        delete(PatreonPendingLink).where(
            col(PatreonPendingLink.consumed_time).is_not(None) | (col(PatreonPendingLink.expiration) <= func.now())
        )
    )
    return result.rowcount or 0


async def delete_pending_links_for_users(session: AsyncSession, tg_user_ids: Collection[int]) -> int:
    """Delete pending links claimed by any of ``tg_user_ids``, and report how many went.

    A claimed row records a Telegram id alongside a Patreon id, which makes it an attributable
    association between the two — brief, but real, and therefore something a deletion request has to
    be able to reach. Nothing else can reach it: there is no foreign key for a cascade to follow.

    Unclaimed rows carry no Telegram identity by construction, so they are not addressable here and
    leave via ``delete_finished_pending_links`` instead.
    """
    if not tg_user_ids:
        return 0
    result = await session.exec(
        delete(PatreonPendingLink).where(col(PatreonPendingLink.claimed_tg_user_id).in_(tg_user_ids))
    )
    return result.rowcount or 0
