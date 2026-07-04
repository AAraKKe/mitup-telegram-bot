"""fix_updated_time_trigger_fires_on_insert

Revision ID: 7b5cead07a89
Revises: 965067fc3f1e
Create Date: 2026-02-28 13:04:27.404127+00:00

"""

from collections.abc import Sequence

from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "7b5cead07a89"
down_revision: str | None = "965067fc3f1e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("users", "settings", "meetups")


def upgrade():
    for table in _TABLES:
        helpers.add_updated_time_trigger(table)


def downgrade():
    for table in _TABLES:
        helpers.remove_updated_time_trigger(table)
        op.execute(
            f"""
            CREATE TRIGGER {table}_updated_time_timestamp
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE set_updated_time();
            """
        )
