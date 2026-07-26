"""cap settings timeout

Cap `settings.timeout` — the minutes a meeting stays active past its end before the deactivation
sweep picks it up — at one day. An unbounded timeout keeps every dated meeting its owner holds
active forever, so those meetings never expire and are never deleted.

Values above the cap are clamped to it first, so the CHECK constraint applies to a table that
already satisfies it. The clamp fires the `settings` updated_time trigger on the rows it touches;
no behaviour reads that column.

The constraint also rejects over-cap writes from the currently deployed image, which is the point:
the settings flow this release bounds is the only way to write one.

Revision ID: 57115114b8f4
Revises: cc628f6f6264
Create Date: 2026-07-26 00:29:45.048817+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "57115114b8f4"
down_revision: str | None = "cc628f6f6264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_settings_timeout_max"
MAX_TIMEOUT_MINUTES = 24 * 60

# Brings every over-cap row down to the cap ahead of the constraint.
CLAMP_SETTINGS_TIMEOUT_SQL = f"""
    UPDATE settings
    SET timeout = {MAX_TIMEOUT_MINUTES}
    WHERE timeout > {MAX_TIMEOUT_MINUTES};
"""


def upgrade():
    op.execute(CLAMP_SETTINGS_TIMEOUT_SQL)
    op.create_check_constraint(CONSTRAINT_NAME, "settings", f"timeout <= {MAX_TIMEOUT_MINUTES}")


def downgrade():
    # Only the constraint comes off: the clamp overwrote the original timeouts in place and they are
    # not recoverable, so a downgrade re-opens the range without restoring any previous value.
    op.drop_constraint(CONSTRAINT_NAME, "settings", type_="check")
