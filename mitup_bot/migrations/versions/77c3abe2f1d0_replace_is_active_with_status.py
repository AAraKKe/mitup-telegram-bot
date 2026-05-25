"""Replace users.is_active with users.status

Swap the boolean `is_active` flag on `users` for a 3-state `status` column
(`member`, `joined_only`, `left`) stored as VARCHAR(16) with a CHECK
constraint. Backfills existing rows top-to-bottom: rows with
`is_active=false` (excluding the phantom sentinel) become `left`; the
sentinel anonymous-invitee row (`tg_user_id = -1`) becomes `joined_only`
so it is excluded from notify CLIs that filter on `status == member`;
all remaining rows keep the server default of `member`.

Storage is plain VARCHAR + CHECK rather than a PostgreSQL ENUM type to avoid
`ALTER TYPE ... DROP VALUE` pain on future status changes. The Python layer
enforces correctness via `UserStatus(StrEnum)`.

Revision ID: 77c3abe2f1d0
Revises: 01e28bf0700d
Create Date: 2026-05-25 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "77c3abe2f1d0"
down_revision: str | None = "01e28bf0700d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="member"),
    )
    op.create_check_constraint(
        "users_status_valid",
        "users",
        "status IN ('member','joined_only','left')",
    )
    op.execute("UPDATE users SET status = 'left' WHERE is_active = false AND tg_user_id != -1;")
    op.execute("UPDATE users SET status = 'joined_only' WHERE tg_user_id = -1;")
    op.drop_column("users", "is_active")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("UPDATE users SET is_active = (status IN ('member','joined_only')) AND tg_user_id != -1;")
    op.drop_constraint("users_status_valid", "users", type_="check")
    op.drop_column("users", "status")
