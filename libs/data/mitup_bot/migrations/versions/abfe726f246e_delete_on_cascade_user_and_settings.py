"""Delete on cascade user and settings

Revision ID: abfe726f246e
Revises: 6ab6bcf55eb7
Create Date: 2024-01-06 16:30:00.195185+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "abfe726f246e"
down_revision: str | None = "6ab6bcf55eb7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_constraint("settings_user_id_fkey", "settings", type_="foreignkey")
    op.create_foreign_key("settings_user_id_fkey", "settings", "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade():
    op.drop_constraint("settings_user_id_fkey", "settings", type_="foreignkey")
    op.create_foreign_key("settings_user_id_fkey", "settings", "users", ["user_id"], ["id"])
