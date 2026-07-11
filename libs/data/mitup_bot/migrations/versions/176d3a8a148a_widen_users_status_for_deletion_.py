"""widen users status for deletion requested

The new `UserStatus.DELETION_REQUESTED` value ("deletion_requested", 18 characters) does not fit
the VARCHAR(16) that backs `users.status` (native_enum=False), so the column is widened to
VARCHAR(32) and the `users_status_valid` CHECK constraint is rebuilt to admit the new value.
No data changes on upgrade.

The downgrade first collapses DELETION_REQUESTED rows to LEFT — the cleanup run already purges
LEFT users, so a pending deletion request keeps its delete-eventually semantics — and only then
restores the three-value constraint and shrinks the column back, which would otherwise fail on
the 18-character value.

Revision ID: 176d3a8a148a
Revises: ebbb61a5128b
Create Date: 2026-07-09 18:40:31.479116+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "176d3a8a148a"
down_revision: str | None = "ebbb61a5128b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        "users",
        "status",
        type_=sa.String(32),
        existing_type=sa.String(16),
        existing_nullable=False,
        existing_server_default="member",
    )
    op.drop_constraint("users_status_valid", "users", type_="check")
    op.create_check_constraint(
        "users_status_valid",
        "users",
        "status IN ('member','joined_only','left','deletion_requested')",
    )


def downgrade():
    op.execute("UPDATE users SET status = 'left' WHERE status = 'deletion_requested'")
    op.drop_constraint("users_status_valid", "users", type_="check")
    op.create_check_constraint(
        "users_status_valid",
        "users",
        "status IN ('member','joined_only','left')",
    )
    op.alter_column(
        "users",
        "status",
        type_=sa.String(16),
        existing_type=sa.String(32),
        existing_nullable=False,
        existing_server_default="member",
    )
