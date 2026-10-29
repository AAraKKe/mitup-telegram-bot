"""store every timestamp with its time zone

Converts every `timestamp` column to `timestamptz`. The stored values are UTC wall clocks, so the
session time zone is pinned to UTC for the conversion to read them as UTC, which also lets
PostgreSQL change the type without rewriting the tables.

Revision ID: 5d732a1e811c
Revises: 5a555b141e04
Create Date: 2026-09-29 22:26:33.481534+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d732a1e811c"
down_revision: str | None = "5a555b141e04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP_COLUMNS = {
    "users": ("created_time", "updated_time", "left_time", "member_time"),
    "settings": ("created_time", "updated_time"),
    "meetups": ("created_time", "updated_time", "expiration_time", "end_datetime", "activated_time", "warned_time"),
    "joined_users": ("created_time",),
    "supporter_subscriptions": ("support_expiration", "created_time", "updated_time"),
    "patreon_creator_tokens": ("token_expiration", "created_time", "updated_time"),
    "broadcasts": ("sending_started_time", "completed_time", "created_time", "updated_time"),
    "broadcast_messages": ("created_time", "updated_time"),
    "broadcast_deliveries": ("sent_time", "next_attempt_time"),
    "patreon_webhooks": ("created_time", "updated_time"),
    "patreon_pending_links": ("expiration", "claimed_time", "consumed_time", "created_time", "updated_time"),
}

# A connection that already fired a trigger holds its plan for the column type it saw, and fails
# every later insert once that type changes. Replacing the functions makes each connection plan again.
TIMESTAMP_FUNCTIONS = (
    """
    CREATE OR REPLACE FUNCTION set_created_time()
    RETURNS TRIGGER AS $$
    BEGIN
        IF NEW.created_time IS NULL THEN
            NEW.created_time := CURRENT_TIMESTAMP;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
    """
    CREATE OR REPLACE FUNCTION set_updated_time()
    RETURNS TRIGGER AS $$
    BEGIN
        IF TG_OP = 'INSERT' AND NEW.updated_time IS NOT NULL THEN
            RETURN NEW;
        END IF;
        NEW.updated_time := CURRENT_TIMESTAMP;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """,
)


def convert_timestamp_columns(target_type: str):
    """Change every listed column to *target_type*, one `ALTER TABLE` per table.

    The lock timeout makes a table held by a long transaction fail the migration instead of queueing
    every query on that table behind it; both settings are reset for the revisions that follow.
    """
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table, columns in TIMESTAMP_COLUMNS.items():
        alterations = ", ".join(f"ALTER COLUMN {column} TYPE {target_type}" for column in columns)
        op.execute(f"ALTER TABLE {table} {alterations}")
    for function in TIMESTAMP_FUNCTIONS:
        op.execute(function)
    op.execute("SET LOCAL lock_timeout TO DEFAULT")
    op.execute("SET LOCAL TIME ZONE DEFAULT")


def upgrade():
    convert_timestamp_columns("timestamptz")


def downgrade():
    convert_timestamp_columns("timestamp")
