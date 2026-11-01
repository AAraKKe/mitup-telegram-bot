"""add onboarding user status

`UserStatus.ONBOARDING` marks a row whose owner has not finished onboarding, so the
`users_status_valid` CHECK constraint is rebuilt to admit it. No data changes on upgrade.

The downgrade moves ONBOARDING rows to MEMBER, the status such a row has without this one, and only
then restores the four-value constraint, which would otherwise reject them.

Revision ID: 4f1c8a2de7b3
Revises: d06736ea31db
Create Date: 2026-11-01 12:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "4f1c8a2de7b3"
down_revision: str | None = "d06736ea31db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_constraint("users_status_valid", "users", type_="check")
    op.create_check_constraint(
        "users_status_valid",
        "users",
        "status IN ('member','joined_only','left','deletion_requested','onboarding')",
    )


def downgrade():
    helpers.execute_bulk(
        revision, "onboarding_to_member", "UPDATE users SET status = 'member' WHERE status = 'onboarding'"
    )
    op.drop_constraint("users_status_valid", "users", type_="check")
    op.create_check_constraint(
        "users_status_valid",
        "users",
        "status IN ('member','joined_only','left','deletion_requested')",
    )
