"""create broadcast tables

Create the mass-broadcast substrate: `broadcasts` (one row per broadcast),
`broadcast_messages` (one row per language) and `broadcast_deliveries` (one
transient row per recipient, bulk-inserted at send start and purged at
finalization). Status columns are stored as plain VARCHAR to match the models'
`native_enum=False` mapping. `broadcasts` and `broadcast_messages` carry the
created/updated timestamp triggers; `broadcast_deliveries` is deliberately
trigger-free (ephemeral, high-insert-volume).

Revision ID: 9784de3c18ab
Revises: 02557bf55f98
Create Date: 2026-07-05 01:59:21.124409+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "9784de3c18ab"
down_revision: str | None = "966e336059a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.BigInteger, nullable=False, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("author_tg_id", sa.BigInteger, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_recipients", sa.Integer, nullable=True),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orphan_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sending_started_time", sa.DateTime, nullable=True),
        sa.Column("completed_time", sa.DateTime, nullable=True),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
    )
    helpers.add_created_time_trigger("broadcasts")
    helpers.add_updated_time_trigger("broadcasts")

    op.create_table(
        "broadcast_messages",
        sa.Column("id", sa.BigInteger, nullable=False, primary_key=True),
        sa.Column("broadcast_id", sa.BigInteger, nullable=False),
        sa.Column("language", sa.String, nullable=False),
        sa.Column("body_html", sa.Text, nullable=False),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orphan_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("broadcast_id", "language", name="uq_broadcast_messages_broadcast_id_language"),
    )
    helpers.add_created_time_trigger("broadcast_messages")
    helpers.add_updated_time_trigger("broadcast_messages")

    op.create_table(
        "broadcast_deliveries",
        sa.Column("id", sa.BigInteger, nullable=False, primary_key=True),
        sa.Column("broadcast_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("language_sent", sa.String, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("sent_time", sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(["broadcast_id"], ["broadcasts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_deliveries_broadcast_id_user_id"),
    )


def downgrade():
    op.drop_table("broadcast_deliveries")
    op.drop_table("broadcast_messages")
    op.drop_table("broadcasts")
