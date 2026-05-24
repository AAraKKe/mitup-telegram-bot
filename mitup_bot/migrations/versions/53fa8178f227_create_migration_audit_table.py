"""create migration_audit table

Revision ID: 53fa8178f227
Revises: a1b2c3d4e5f6
Create Date: 2026-05-16 18:57:40.377490+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "53fa8178f227"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "migration_audit",
        sa.Column("table_name", sa.String, primary_key=True, nullable=False),
        sa.Column("old_id", sa.BigInteger, primary_key=True, nullable=False),
        sa.Column("new_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_migration_audit_status", "migration_audit", ["status"])


def downgrade() -> None:
    op.drop_index("ix_migration_audit_status", table_name="migration_audit")
    op.drop_table("migration_audit")
