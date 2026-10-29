import importlib.util
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection, NullPool, create_engine, text

import mitup_bot.db
from mitup_bot.config import DbConfig
from mitup_bot.db import build_db_url

pytestmark = pytest.mark.db_test

MIGRATION_PATH = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(mitup_bot.db.__file__).parent
    / "migrations"
    / "versions"
    / "5d732a1e811c_store_every_timestamp_with_its_time_zone.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_timestamptz", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = load_migration()
CONVERTED_COLUMNS = {(table, column) for table, columns in MIGRATION.TIMESTAMP_COLUMNS.items() for column in columns}
ALREADY_ZONED_COLUMNS = {("meetups", "datetime"), ("migration_audit", "created_at")}

SCRATCH_DATABASE = "timestamptz_migration_probe"
# Clocks in Pacific/Auckland skip 02:00-03:00 on 2026-09-27 and repeat 02:00-03:00 on 2026-04-05.
DAYLIGHT_SAVING_GAP = "2026-09-27 02:30:00.000000"
DAYLIGHT_SAVING_FOLD = "2026-04-05 02:30:00.000000"

STORED_WALL_CLOCKS = {
    ("users", "created_time"): "2026-01-02 03:04:05.123456",
    ("users", "updated_time"): DAYLIGHT_SAVING_GAP,
    ("users", "left_time"): None,
    ("users", "member_time"): DAYLIGHT_SAVING_FOLD,
    ("meetups", "created_time"): "2026-02-10 23:59:59.999999",
    ("meetups", "updated_time"): DAYLIGHT_SAVING_FOLD,
    ("meetups", "expiration_time"): None,
    ("meetups", "end_datetime"): DAYLIGHT_SAVING_GAP,
    ("meetups", "activated_time"): "2026-06-30 12:00:00.000000",
    ("meetups", "warned_time"): None,
    ("joined_users", "created_time"): DAYLIGHT_SAVING_GAP,
}
MEETING_START = "2026-07-15 18:30:00.000000"
AUDIT_CREATED_AT = "2026-05-16 19:00:00.000000"

INSERT_PROBE_ROWS = [
    """
    INSERT INTO users (id, tg_user_id, first_name, status, created_time, updated_time, left_time, member_time)
    VALUES (1, 996001, 'timestamptz-probe', 'member',
            :users_created_time, :users_updated_time, :users_left_time, :users_member_time)
    """,
    """
    INSERT INTO meetups (
        id, owner_id, title, waiting_list, public, allow_invitation, incognito,
        expiration_notification_sent, started_notification_sent, lock_on_start, active,
        created_time, updated_time, expiration_time, end_datetime, activated_time, warned_time, datetime
    ) VALUES (1, 1, 'timestamptz-probe', false, false, false, false, false, false, false, true,
              :meetups_created_time, :meetups_updated_time, :meetups_expiration_time, :meetups_end_datetime,
              :meetups_activated_time, :meetups_warned_time, :meeting_start)
    """,
    """
    INSERT INTO joined_users (id, user_id, meetup_id, is_waiting_list, notification_sent, created_time)
    VALUES (1, 1, 1, false, false, :joined_users_created_time)
    """,
    """
    INSERT INTO migration_audit (table_name, old_id, status, created_at)
    VALUES ('timestamptz-probe', 1, 'probe', :audit_created_at)
    """,
]


@pytest.fixture
def scratch_database(live_db_config: DbConfig, monkeypatch: pytest.MonkeyPatch) -> Generator[DbConfig]:
    """A throwaway database in the suite's container, with Alembic pointed at it."""
    admin_engine = create_engine(build_db_url(live_db_config), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as admin:
        admin.execute(text(f"CREATE DATABASE {SCRATCH_DATABASE}"))
    scratch_config = live_db_config.model_copy(update={"database": SCRATCH_DATABASE})
    for name, value in {
        "MITUPBOT__DB__USERNAME": scratch_config.username,
        "MITUPBOT__DB__PASSWORD": scratch_config.password.get_secret_value(),
        "MITUPBOT__DB__URL": scratch_config.url,
        "MITUPBOT__DB__DATABASE": scratch_config.database,
        "MITUPBOT__DB__PORT": str(scratch_config.port),
    }.items():
        monkeypatch.setenv(name, value)
    try:
        yield scratch_config
    finally:
        with admin_engine.connect() as admin:
            admin.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DATABASE} WITH (FORCE)"))
        admin_engine.dispose()


def read_wall_clocks(connection: Connection, rendering: str) -> dict[tuple[str, str], str | None]:
    """Each probed column's value as PostgreSQL renders it through *rendering*, a template on `{column}`."""
    return {
        (table, column): connection.execute(
            text(f"SELECT {rendering.format(column=column)} FROM {table} WHERE id = 1")
        ).scalar_one()
        for table, column in STORED_WALL_CLOCKS
    }


def read_zoned_values(connection: Connection) -> tuple[str, str]:
    utc_text = "to_char({} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')"
    meeting_start = connection.execute(text(f"SELECT {utc_text.format('datetime')} FROM meetups WHERE id = 1"))
    audit_created_at = connection.execute(
        text(f"SELECT {utc_text.format('created_at')} FROM migration_audit WHERE old_id = 1")
    )
    return meeting_start.scalar_one(), audit_created_at.scalar_one()


def read_column_types(connection: Connection) -> dict[tuple[str, str], str]:
    rows = connection.execute(
        text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns"
            " WHERE table_schema = 'public' AND data_type LIKE 'timestamp%'"
        )
    ).all()
    return {(table, column): data_type for table, column, data_type in rows}


def test_conversion_keeps_every_instant_when_the_database_runs_in_another_zone(scratch_database: DbConfig):
    alembic_config = AlembicConfig("alembic.ini")
    command.upgrade(alembic_config, MIGRATION.down_revision)
    # Only a connection opened after the ALTER DATABASE starts in the zone it sets.
    engine = create_engine(build_db_url(scratch_database), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER DATABASE {SCRATCH_DATABASE} SET timezone = 'Pacific/Auckland'"))
        with engine.begin() as connection:
            assert connection.execute(text("SHOW TIME ZONE")).scalar_one() == "Pacific/Auckland"
            values = {f"{table}_{column}": value for (table, column), value in STORED_WALL_CLOCKS.items()}
            values |= {"meeting_start": f"{MEETING_START}+00", "audit_created_at": f"{AUDIT_CREATED_AT}+00"}
            for statement in INSERT_PROBE_ROWS:
                connection.execute(text(statement), values)

        command.upgrade(alembic_config, MIGRATION.revision)

        with engine.begin() as connection:
            assert read_wall_clocks(connection, "to_char({column} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')") == (
                STORED_WALL_CLOCKS
            )
            assert read_zoned_values(connection) == (MEETING_START, AUDIT_CREATED_AT)
            column_types = read_column_types(connection)
            assert set(column_types) == CONVERTED_COLUMNS | ALREADY_ZONED_COLUMNS
            assert set(column_types.values()) == {"timestamp with time zone"}
            stamped_by_trigger = connection.execute(
                text(
                    "INSERT INTO users (id, tg_user_id, first_name, status)"
                    " VALUES (2, 996002, 'timestamptz-probe', 'member') RETURNING created_time = now()"
                )
            ).scalar_one()
            assert stamped_by_trigger is True

        command.downgrade(alembic_config, MIGRATION.down_revision)

        with engine.begin() as connection:
            assert read_wall_clocks(connection, "to_char({column}, 'YYYY-MM-DD HH24:MI:SS.US')") == STORED_WALL_CLOCKS
            assert read_zoned_values(connection) == (MEETING_START, AUDIT_CREATED_AT)
            column_types = read_column_types(connection)
            assert {column_types[column] for column in CONVERTED_COLUMNS} == {"timestamp without time zone"}
            assert {column_types[column] for column in ALREADY_ZONED_COLUMNS} == {"timestamp with time zone"}
    finally:
        engine.dispose()


def join_new_member(connection: Connection, user_id: int) -> tuple[bool, bool]:
    """Insert a user and their membership of meeting 1, reporting whether each trigger stamped `created_time`."""
    user_stamped = connection.execute(
        text(
            "INSERT INTO users (id, tg_user_id, first_name, status) VALUES (:id, :id, 'stale-plan-probe', 'member')"
            " RETURNING created_time = now()"
        ),
        {"id": user_id},
    ).scalar_one()
    membership_stamped = connection.execute(
        text(
            "INSERT INTO joined_users (user_id, meetup_id, is_waiting_list, notification_sent)"
            " VALUES (:id, 1, false, false) RETURNING created_time = now()"
        ),
        {"id": user_id},
    ).scalar_one()
    connection.commit()
    return user_stamped, membership_stamped


def test_open_connections_keep_inserting_across_the_conversion(scratch_database: DbConfig):
    alembic_config = AlembicConfig("alembic.ini")
    command.upgrade(alembic_config, MIGRATION.down_revision)
    engine = create_engine(build_db_url(scratch_database))
    try:
        with engine.connect() as open_connection:
            open_connection.execute(
                text(
                    "INSERT INTO users (id, tg_user_id, first_name, status)"
                    " VALUES (996100, 996100, 'stale-plan-probe', 'member');"
                    " INSERT INTO meetups (id, owner_id, title, waiting_list, public, allow_invitation, incognito,"
                    " expiration_notification_sent, started_notification_sent, lock_on_start, active)"
                    " VALUES (1, 996100, 'stale-plan-probe', false, false, false, false, false, false, false, true)"
                )
            )
            open_connection.commit()
            assert join_new_member(open_connection, 996_101) == (True, True)

            command.upgrade(alembic_config, MIGRATION.revision)
            assert join_new_member(open_connection, 996_102) == (True, True)

            command.downgrade(alembic_config, MIGRATION.down_revision)
            assert join_new_member(open_connection, 996_103) == (True, True)
    finally:
        engine.dispose()
