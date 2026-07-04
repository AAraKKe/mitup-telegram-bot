import inspect
import os
import re
from collections.abc import AsyncGenerator, Generator

import docker
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession
from testcontainers.postgres import PostgresContainer

from mitup_bot import db
from mitup_bot.config import DbConfig
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.monitoring import MetricsClient
from tests.helpers import make_test_metrics_client

USERNAME = "mitupbot"
PAST_MEETINGS = "12345pass"


def _read_postgres_image() -> str:
    with open("docker-compose.yaml") as f:
        content = f.read()
    match = re.search(r"image:\s*(postgres:\S+)", content)
    if match is None:
        raise RuntimeError("Could not extract postgres image from docker-compose.yaml")
    return match.group(1)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--db-tests", action="store_true", default=False, help="Run DB integration tests")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--db-tests"):
        skip = pytest.mark.skip(reason="Pass --db-tests to run database integration tests")
        for item in items:
            if item.get_closest_marker("db_test"):
                item.add_marker(skip)
    else:
        try:
            docker.from_env().ping()
        except Exception as exc:
            pytest.fail(f"Docker is unavailable but --db-tests was requested: {exc}")

    # The db_session fixture (and its seeds) are session-scoped async fixtures living on the
    # session event loop, so every async db test must run on that same loop.
    for item in items:
        if item.get_closest_marker("db_test") and inspect.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio(loop_scope="session"), append=False)


@pytest.fixture(scope="session")
def pg_container() -> Generator[PostgresContainer]:
    image = _read_postgres_image()
    container = PostgresContainer(
        image=image,
        username=USERNAME,
        password=PAST_MEETINGS,
        dbname="mitup",
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def live_db_config(pg_container: PostgresContainer) -> DbConfig:
    return DbConfig(
        username=USERNAME,
        password=SecretStr(PAST_MEETINGS),
        url=pg_container.get_container_host_ip(),
        database="mitup",
        port=int(pg_container.get_exposed_port(5432)),
    )


@pytest.fixture(scope="session")
def migrated_db(live_db_config: DbConfig) -> Generator[DbConfig]:
    # Alembic keeps its own synchronous engine, so the migration run stays sync.
    saved: dict[str, str | None] = {}
    env_vars = {
        "MITUPBOT__DB__USERNAME": live_db_config.username,
        "MITUPBOT__DB__PASSWORD": live_db_config.password.get_secret_value(),
        "MITUPBOT__DB__URL": live_db_config.url,
        "MITUPBOT__DB__DATABASE": live_db_config.database,
        "MITUPBOT__DB__PORT": str(live_db_config.port),
    }
    for key, value in env_vars.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        alembic_cfg = AlembicConfig("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        yield live_db_config
    finally:
        for key, original_value in saved.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value


@pytest.fixture(scope="session")
def pool_metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_session(migrated_db: DbConfig, pool_metrics_client: MetricsClient) -> AsyncGenerator[AsyncSession]:
    # The recording client instruments the real pool so integration tests can assert the
    # pool metrics emitted by actual checkouts (see test_pool_metrics.py).
    db.configure_db(migrated_db, skip_if_initialized=True, metrics_client=pool_metrics_client)
    async with db.begin() as session:
        yield session


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_user(db_session: AsyncSession) -> User:
    user = User(first_name="Seed User One", tg_user_id=999_001)
    user.settings = Settings()
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_second_user(db_session: AsyncSession) -> User:
    user = User(first_name="Seed User Two", tg_user_id=999_002)
    user.settings = Settings()
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_meetup(db_session: AsyncSession, seed_user: User) -> Meetup:
    meetup = Meetup(
        title="Seed Meetup",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=seed_user,
    )
    db_session.add(meetup)
    await db_session.flush()
    return meetup


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def seed_joined_link(db_session: AsyncSession, seed_second_user: User, seed_meetup: Meetup) -> JoinedUsers:
    joined = JoinedUsers(
        user=seed_second_user,
        meetup=seed_meetup,
    )
    db_session.add(joined)
    await db_session.flush()
    return joined
