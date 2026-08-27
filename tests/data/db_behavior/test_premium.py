import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot.db
from mitup_bot.models import PatreonCreatorToken, Settings, SupporterSubscription, User, configure_token_encryption
from mitup_bot.models.subscriptions import TokenCipher
from mitup_bot.supporter import SupporterLevel
from tests.helpers import create_patreon_creator_token, create_supporter_subscription

pytestmark = pytest.mark.db_test

# mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
MIGRATIONS_DIR = Path(mitup_bot.db.__file__).parent / "migrations" / "versions"
SUPPORTER_LEVEL_MIGRATION_PATH = MIGRATIONS_DIR / "c459065f341a_replace_users_is_premium_with_users_.py"
HOST_LEVEL_RENAME_MIGRATION_PATH = MIGRATIONS_DIR / "0d0d349b705a_rename_supporter_tiers_to_host_levels.py"


def load_migration(module_name: str, path: Path) -> ModuleType:
    """Load a revision module by file path (its name starts with a digit, so no dotted import) to
    reuse the exact data-migration SQL, keeping this test in lockstep with the migration."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUPPORTER_LEVEL_MIGRATION = load_migration("_migration_c459065f341a", SUPPORTER_LEVEL_MIGRATION_PATH)
HOST_LEVEL_RENAME_MIGRATION = load_migration("_migration_0d0d349b705a", HOST_LEVEL_RENAME_MIGRATION_PATH)


@pytest.fixture(autouse=True, scope="module")
def configured_token_encryption() -> Iterator[None]:
    """Encrypt/decrypt the token columns with a throwaway key for the duration of this module."""
    saved = TokenCipher.cipher
    configure_token_encryption(Fernet.generate_key().decode())
    try:
        yield
    finally:
        TokenCipher.cipher = saved


async def new_user(db_session: AsyncSession, tg_user_id: int) -> User:
    user = User(first_name="Premium Test", tg_user_id=tg_user_id, settings=Settings())
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.parametrize("table_name", ["supporter_subscriptions", "patreon_creator_tokens"])
async def test_table_exists(db_session: AsyncSession, table_name: str):
    result = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=:name"
            ).bindparams(name=table_name)
        )
    ).scalar_one()
    assert result == 1


async def test_users_has_supporter_level_column(db_session: AsyncSession):
    is_nullable, data_type, column_default = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT is_nullable, data_type, column_default FROM information_schema.columns"
                " WHERE table_name='users' AND column_name='supporter_level'"
            )
        )
    ).one()
    assert is_nullable == "NO"
    assert data_type == "character varying"
    assert column_default is not None and "none" in column_default


async def test_supporter_level_defaults_to_none(db_session: AsyncSession):
    user = await new_user(db_session, 998_756)
    await db_session.refresh(user)
    assert user.supporter_level is SupporterLevel.NONE


async def test_supporter_level_rejects_unknown_value(db_session: AsyncSession, seed_user: User):
    """The CHECK constraint the migration installs guards the column against tiers outside the enum.

    ``seed_user`` is requested rather than assumed: the row it creates is the one this UPDATE aims
    at, and an UPDATE matching nothing violates no constraint, so without the fixture the test would
    pass or fail on whether some other test on the same xdist worker had already pulled the seed in.
    """
    savepoint = await db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("UPDATE users SET supporter_level = 'gold' WHERE tg_user_id = :t").bindparams(
                    t=seed_user.tg_user_id
                )
            )
            await db_session.flush()
    finally:
        await savepoint.rollback()


async def test_migration_grandfathers_premium_users_to_host_2(db_session: AsyncSession):
    """Premium users end up on the HOST_2 tier through the real two-migration sequence: c459065
    grandfathers `is_premium=true` rows onto the (then-named) `patron` value, and the follow-up
    rename migration `0d0d349b705a` rewrites that to `host_2`. Everyone else stays at NONE.

    The live schema is already at head, so its CHECK constraint only admits the host-level values;
    we drop it inside the savepoint to write c459065's intermediate `patron` value before the rename
    step maps it forward. The savepoint rollback restores the constraint."""
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("ALTER TABLE users ADD COLUMN is_premium boolean NOT NULL DEFAULT false")
        )
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("ALTER TABLE users DROP CONSTRAINT users_supporter_level_valid")
        )
        premium_user = await new_user(db_session, 998_770)
        free_user = await new_user(db_session, 998_771)
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("UPDATE users SET is_premium = true WHERE tg_user_id = :t").bindparams(t=998_770)
        )

        await db_session.exec(text(SUPPORTER_LEVEL_MIGRATION.GRANDFATHER_PREMIUM_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        await db_session.exec(text(HOST_LEVEL_RENAME_MIGRATION.RENAME_TO_HOST_LEVELS_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        await db_session.flush()
        await db_session.refresh(premium_user)
        await db_session.refresh(free_user)

        assert premium_user.supporter_level is SupporterLevel.HOST_2
        assert free_user.supporter_level is SupporterLevel.NONE
    finally:
        await savepoint.rollback()


async def test_migration_downgrade_reverses_any_tier_to_premium(db_session: AsyncSession):
    """The downgrade's data step maps any paying tier back to `is_premium=true` and NONE to false."""
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("ALTER TABLE users ADD COLUMN is_premium boolean NOT NULL DEFAULT false")
        )
        organizer = await new_user(db_session, 998_772)
        organizer.supporter_level = SupporterLevel.HOST_3
        patron = await new_user(db_session, 998_773)
        patron.supporter_level = SupporterLevel.HOST_2
        await new_user(db_session, 998_774)
        await db_session.flush()

        await db_session.exec(text(SUPPORTER_LEVEL_MIGRATION.REVERSE_TO_BOOLEAN_SQL))  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        await db_session.flush()

        premium_flags = {
            tg_user_id: (
                await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("SELECT is_premium FROM users WHERE tg_user_id = :t").bindparams(t=tg_user_id)
                )
            ).scalar_one()
            for tg_user_id in (998_772, 998_773, 998_774)
        }
        assert premium_flags == {998_772: True, 998_773: True, 998_774: False}
    finally:
        await savepoint.rollback()


async def test_patreon_creator_token_encrypts_tokens_at_rest(db_session: AsyncSession):
    token_row = create_patreon_creator_token(
        access_token="plain-creator-access",
        refresh_token="plain-creator-refresh",
        seed_fingerprint="fingerprint-abc",
    )
    db_session.add(token_row)
    await db_session.flush()
    token_id = token_row.db_id

    db_session.expire(token_row)
    loaded = (await db_session.exec(select(PatreonCreatorToken).where(PatreonCreatorToken.id == token_id))).one()
    assert loaded.access_token == "plain-creator-access"
    assert loaded.refresh_token == "plain-creator-refresh"
    assert loaded.seed_fingerprint == "fingerprint-abc"

    stored_refresh = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("SELECT refresh_token FROM patreon_creator_tokens WHERE id = :id").bindparams(id=token_id)
        )
    ).scalar_one()
    assert stored_refresh != "plain-creator-refresh"
    assert TokenCipher.decrypt(stored_refresh) == "plain-creator-refresh"


async def test_user_id_is_unique(db_session: AsyncSession):
    user = await new_user(db_session, 998_751)
    db_session.add(create_supporter_subscription(user_id=user.db_id, patreon_user_id="patreon-998751-a"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(create_supporter_subscription(user_id=user.db_id, patreon_user_id="patreon-998751-b"))
            await db_session.flush()


async def test_patreon_user_id_is_unique(db_session: AsyncSession):
    first_user = await new_user(db_session, 998_752)
    second_user = await new_user(db_session, 998_753)
    db_session.add(create_supporter_subscription(user_id=first_user.db_id, patreon_user_id="patreon-shared"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(create_supporter_subscription(user_id=second_user.db_id, patreon_user_id="patreon-shared"))
            await db_session.flush()


async def test_deleting_user_cascades_to_premium_subscription(db_session: AsyncSession):
    user = await new_user(db_session, 998_755)
    db_session.add(create_supporter_subscription(user_id=user.db_id, patreon_user_id="patreon-998755"))
    await db_session.flush()
    user_id = user.db_id

    await db_session.delete(user)
    await db_session.flush()

    remaining = (
        await db_session.exec(select(SupporterSubscription).where(SupporterSubscription.user_id == user_id))
    ).all()
    assert remaining == []


async def test_premium_subscription_timestamps_set_on_insert(db_session: AsyncSession):
    user = await new_user(db_session, 998_757)
    subscription = create_supporter_subscription(user_id=user.db_id, patreon_user_id="patreon-998757")
    db_session.add(subscription)
    await db_session.flush()
    await db_session.refresh(subscription)

    assert subscription.created_time is not None
    assert subscription.updated_time is not None


async def test_patreon_creator_token_timestamps_set_on_insert(db_session: AsyncSession):
    token_row = create_patreon_creator_token(seed_fingerprint="fingerprint-998758")
    db_session.add(token_row)
    await db_session.flush()
    await db_session.refresh(token_row)

    assert token_row.created_time is not None
    assert token_row.updated_time is not None


async def test_expiration_notified_defaults_to_false(db_session: AsyncSession):
    user = await new_user(db_session, 998_759)
    subscription = create_supporter_subscription(user_id=user.db_id, patreon_user_id="patreon-998759")
    db_session.add(subscription)
    await db_session.flush()
    await db_session.refresh(subscription)

    assert subscription.expiration_notified is False
