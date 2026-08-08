"""add users granted_supporter_level

Add `users.granted_supporter_level`, the manually-granted floor for `supporter_level`
set through the admin grant flow. Storage mirrors `supporter_level`: plain VARCHAR(16)
with a CHECK constraint rather than a PostgreSQL ENUM type, so future tier changes
never need `ALTER TYPE`. Every row defaults to `none` (no grant), which keeps the
column purely additive and safe for the currently-deployed image.

Revision ID: 56be745b7ded
Revises: ed11fdab83a6
Create Date: 2026-08-07 15:57:43.155886+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "56be745b7ded"
down_revision: str | None = "ed11fdab83a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("granted_supporter_level", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.create_check_constraint(
        "users_granted_supporter_level_valid",
        "users",
        "granted_supporter_level IN ('none','host_1','host_2','host_3')",
    )


def downgrade():
    op.drop_constraint("users_granted_supporter_level_valid", "users", type_="check")
    op.drop_column("users", "granted_supporter_level")
