"""Rename languaje column to language in settings table

Revision ID: 955165f08a88
Revises: 8c24a14289b5
Create Date: 2024-06-01 14:41:22.092811+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "955165f08a88"
down_revision: str | None = "8c24a14289b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("settings", "languaje", new_column_name="language")


def downgrade() -> None:
    op.alter_column("settings", "language", new_column_name="languaje")
