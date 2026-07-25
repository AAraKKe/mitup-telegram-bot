"""Add patreon pending links table

Adds `patreon_pending_links`, which parks a Patreon identity proven by OAuth consent until a Telegram
account claims and confirms it with the pairing code. Only the SHA-256 of that code is stored, and it
is unique so a claim is never ambiguous. `claimed_tg_user_id` records who presented the code and
`consumed_time` marks the row spent; both transitions are conditional updates filtering on
`consumed_time IS NULL`, which is what makes a code single use. `claimed_tg_user_id` is deliberately
non-unique: one person can hold several live rows. It is a BigInteger to match `users.tg_user_id`.
`supporter_level` mirrors the `users` column: `native_enum=False` on the model side, so the DDL is a
plain VARCHAR rather than a PostgreSQL enum type. `created_time`/`updated_time` are owned by the
shared timestamp triggers, matching every other table.

Purely additive, so the currently deployed image runs against this schema unchanged.

Revision ID: cc628f6f6264
Revises: 26719527cf8f
Create Date: 2026-07-25 01:08:32.107179+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "cc628f6f6264"
down_revision: str | None = "26719527cf8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "patreon_pending_links",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("code_hash", sa.String, nullable=False, unique=True),
        sa.Column("patreon_user_id", sa.String, nullable=False),
        sa.Column("patreon_full_name", sa.String, nullable=True),
        sa.Column("supporter_level", sa.String(16), nullable=False, server_default="none"),
        sa.Column("expiration", sa.DateTime, nullable=False),
        sa.Column("claimed_tg_user_id", sa.BigInteger, nullable=True),
        sa.Column("claimed_time", sa.DateTime, nullable=True),
        sa.Column("consumed_time", sa.DateTime, nullable=True),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
    )
    helpers.add_created_time_trigger("patreon_pending_links")
    helpers.add_updated_time_trigger("patreon_pending_links")


def downgrade():
    helpers.remove_created_time_trigger("patreon_pending_links")
    helpers.remove_updated_time_trigger("patreon_pending_links")
    op.drop_table("patreon_pending_links")
