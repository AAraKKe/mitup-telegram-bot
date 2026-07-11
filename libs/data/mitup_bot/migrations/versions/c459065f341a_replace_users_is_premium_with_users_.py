"""replace users.is_premium with users.supporter_level

Swap the boolean `is_premium` flag on `users` for a 4-state `supporter_level` column
(`none`, `supporter`, `patron`, `organizer`) stored as VARCHAR(16) with a CHECK
constraint. Existing premium users were promised the raised (Tier-2) limits, so they
are grandfathered onto `patron`; every other row keeps the server default of `none`.

Storage is plain VARCHAR + CHECK rather than a PostgreSQL ENUM type to avoid
`ALTER TYPE ... DROP VALUE` pain on future tier changes, mirroring `users.status`.
The Python layer enforces correctness via `SupporterLevel(StrEnum)`.

The downgrade restores the boolean: any paying tier (`supporter_level != 'none'`)
maps back to `is_premium = true`, so the reversal is lossy across tiers (a
grandfathered patron and an organizer both round-trip to premium) but preserves the
original boolean semantics.

Revision ID: c459065f341a
Revises: 9784de3c18ab
Create Date: 2026-07-06 18:26:56.946327+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c459065f341a"
down_revision: str | None = "9784de3c18ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Grandfathering: existing premium users were promised the Tier-2 (Patron) limits.
GRANDFATHER_PREMIUM_SQL = "UPDATE users SET supporter_level = 'patron' WHERE is_premium = true;"
# Reverse: any paying tier round-trips back to the premium boolean (lossy across tiers).
REVERSE_TO_BOOLEAN_SQL = "UPDATE users SET is_premium = (supporter_level != 'none');"


def upgrade():
    op.add_column(
        "users",
        sa.Column("supporter_level", sa.String(length=16), nullable=False, server_default="none"),
    )
    op.create_check_constraint(
        "users_supporter_level_valid",
        "users",
        "supporter_level IN ('none','supporter','patron','organizer')",
    )
    op.execute(GRANDFATHER_PREMIUM_SQL)
    op.drop_column("users", "is_premium")


def downgrade():
    op.add_column(
        "users",
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(REVERSE_TO_BOOLEAN_SQL)
    op.drop_constraint("users_supporter_level_valid", "users", type_="check")
    op.drop_column("users", "supporter_level")
