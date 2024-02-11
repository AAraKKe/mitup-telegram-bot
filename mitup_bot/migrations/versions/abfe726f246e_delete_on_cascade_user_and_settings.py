"""Delete on cascade user and settings

Revision ID: abfe726f246e
Revises: 741e3b4adacf
Create Date: 2024-01-06 16:30:00.195185+00:00

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "abfe726f246e"
down_revision: str | None = "741e3b4adacf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("settings_user_id_fkey", "settings", type_="foreignkey")
    op.create_foreign_key("settings_user_id_fkey", "settings", "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("settings_user_id_fkey", "settings", type_="foreignkey")
    op.create_foreign_key("settings_user_id_fkey", "settings", "users", ["user_id"], ["id"])
