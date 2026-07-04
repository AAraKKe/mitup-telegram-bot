"""Pool observability against a real Postgres pool.

The listeners attached by `db.instrument_pool` fire from SQLAlchemy's pool internals, so the
honest coverage is real checkouts on a real engine. The pool-timeout counter is covered as a
unit test in tests/test_db.py instead: saturating the shared session-scoped pool would need
pool_size + max_overflow concurrent transactions plus a pool_timeout-long wait.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.config import DbConfig
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from tests.helpers import make_test_metrics_client
from tests.helpers.monitoring import MetricAssertions

pytestmark = pytest.mark.db_test


@pytest.fixture
def pool_metrics(db_session: AsyncSession, pool_metrics_client: MetricsClient) -> MetricAssertions:
    # The session-scoped client accumulates records across every db test on this worker;
    # start each assertion window clean. Depending on db_session keeps the fixture ordering
    # deterministic: the engine (and its held seed connection) exists before the clear.
    pool_metrics_client.records.clear()
    return MetricAssertions(pool_metrics_client)


async def test_begin_emits_gauges_and_wait_time_on_real_checkout(
    db_session: AsyncSession, pool_metrics_client: MetricsClient, pool_metrics: MetricAssertions
):
    async with db.begin() as session:
        await session.exec(text("SELECT 1"))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657

    # One checkout and one checkin for the transaction's connection...
    pool_metrics.assert_emitted(name=MetricKey.DB_POOL_CONNECTIONS_IN_USE, value=None, times=2)
    # ...and the wait for the checkout was measured.
    pool_metrics.assert_emitted(
        name=MetricKey.DB_POOL_CHECKOUT_WAIT_TIME, value=None, unit=MetricUnit.MILLISECONDS, times=1
    )


async def test_instrumented_pool_reports_opens_and_in_use_levels(live_db_config: DbConfig):
    """Two overlapping checkouts on a fresh single-connection pool walk the in-use gauge
    1 → 2 → 1 → 0 and open exactly two physical connections (the second via overflow)."""
    engine = create_async_engine(live_db_config.full_url, pool_size=1, max_overflow=1)
    recording_client = make_test_metrics_client()
    db.instrument_pool(engine, recording_client)

    try:
        async with engine.connect() as first_connection:
            await first_connection.execute(text("SELECT 1"))
            async with engine.connect() as second_connection:
                await second_connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()

    pool_assertions = MetricAssertions(recording_client)
    pool_assertions.assert_emitted(name=MetricKey.DB_POOL_CONNECTIONS_OPENED, value=1, times=2)
    pool_assertions.assert_emitted(name=MetricKey.DB_POOL_CONNECTIONS_IN_USE, value=2, times=1)
    pool_assertions.assert_emitted(name=MetricKey.DB_POOL_CONNECTIONS_IN_USE, value=0, times=1)
