"""rename supporter tiers to host levels

Decouple the internal supporter-tier identifiers from their marketing display names: the
stored `users.supporter_level` values `supporter`/`patron`/`organizer` become the
display-agnostic `host_1`/`host_2`/`host_3` (`none` is unchanged). The prior migration
`c459065f341a` grandfathered premium users onto `patron`, so those rows now become
`host_2`. The CHECK constraint is dropped and recreated against the new value set.

Storage stays plain VARCHAR(16) + CHECK (mirroring `users.status`); the Python layer
enforces correctness via `SupporterLevel(StrEnum)`. The downgrade is symmetric, mapping
the host levels back to their former names and restoring the old CHECK.

Revision ID: 0d0d349b705a
Revises: 1b3befe3e1c2
Create Date: 2026-07-07 18:58:43.750148+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d0d349b705a"
down_revision: str | None = "1b3befe3e1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAME_TO_HOST_LEVELS_SQL = """
UPDATE users SET supporter_level = CASE supporter_level
    WHEN 'supporter' THEN 'host_1'
    WHEN 'patron' THEN 'host_2'
    WHEN 'organizer' THEN 'host_3'
    ELSE supporter_level
END;
"""
REVERSE_TO_TIER_NAMES_SQL = """
UPDATE users SET supporter_level = CASE supporter_level
    WHEN 'host_1' THEN 'supporter'
    WHEN 'host_2' THEN 'patron'
    WHEN 'host_3' THEN 'organizer'
    ELSE supporter_level
END;
"""


def upgrade():
    op.drop_constraint("users_supporter_level_valid", "users", type_="check")
    op.execute(RENAME_TO_HOST_LEVELS_SQL)
    op.create_check_constraint(
        "users_supporter_level_valid",
        "users",
        "supporter_level IN ('none','host_1','host_2','host_3')",
    )


def downgrade():
    op.drop_constraint("users_supporter_level_valid", "users", type_="check")
    op.execute(REVERSE_TO_TIER_NAMES_SQL)
    op.create_check_constraint(
        "users_supporter_level_valid",
        "users",
        "supporter_level IN ('none','supporter','patron','organizer')",
    )
