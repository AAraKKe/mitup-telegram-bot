import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from tests.helpers.stub_db import MockDbSession

# How long the lock holder keeps its transaction open for the contender to act. With the row lock
# in place the contender blocks and the window always elapses in full; without it the contender
# finishes inside the window against the holder's uncommitted state — the interleaving the lock
# forbids — which is what makes the race tests fail if for_update is ever dropped.
CONTENDER_WINDOW = 0.5
# Hard cap so a lock bug (e.g. a deadlock) fails the test quickly instead of hanging the suite.
RACE_TIMEOUT = 20.0


async def race[H, C](
    holder: Callable[[AsyncSession], Awaitable[H]],
    contender: Callable[[AsyncSession], Awaitable[C]],
) -> tuple[H, C]:
    """Run two transactions with a deterministic interleaving and return their results.

    The holder runs its critical section first (taking the row lock inside its own code), then
    keeps its transaction open for up to CONTENDER_WINDOW while the contender runs. With the lock
    in place the contender blocks inside its locked load until the holder commits, so it always
    observes the holder's committed state; without the lock the contender completes inside the
    window against uncommitted state, corrupting the final state the race tests assert on.
    """
    holder_mutated = asyncio.Event()
    contender_finished = asyncio.Event()

    async def run_holder() -> H:
        async with db.begin() as session:
            result = await holder(session)
            holder_mutated.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(contender_finished.wait(), timeout=CONTENDER_WINDOW)
        return result

    async def run_contender() -> C:
        await holder_mutated.wait()
        try:
            async with db.begin() as session:
                return await contender(session)
        finally:
            contender_finished.set()

    async with asyncio.timeout(RACE_TIMEOUT):
        holder_result, contender_result = await asyncio.gather(run_holder(), run_contender())
    return holder_result, contender_result


def assert_locked_meetup_select(mock_session: MockDbSession):
    """Assert the handler loaded the meeting under the per-meeting row lock (issue #187).

    Deliberately tolerant of dialect/formatting details: every executed meetups SELECT must carry
    FOR UPDATE. Flows that legitimately pre-read the meeting unlocked (e.g. earlier conversation
    steps) should reset ``mock_session.exec`` before the mutating step so only its queries remain.
    """
    meetup_selects = [
        query
        for query in mock_session.queries_executed
        if query.lower().startswith("select") and "from meetups" in query.lower()
    ]
    assert meetup_selects, "expected the handler to SELECT the meeting from the meetups table"
    unlocked = [query for query in meetup_selects if "FOR UPDATE" not in query]
    assert not unlocked, f"meetups SELECT(s) missing FOR UPDATE: {unlocked}"
