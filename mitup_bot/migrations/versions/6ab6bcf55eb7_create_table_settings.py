"""Create table settings

Revision ID: 6ab6bcf55eb7
Revises: 5731b33b7fc0
Create Date: 2023-12-02 18:42:44.211510+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ab6bcf55eb7"
down_revision: str | None = "5731b33b7fc0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("created_time", sa.TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_time", sa.TIMESTAMP, nullable=False, server_default=sa.func.now()),
        sa.Column("languaje", sa.String, nullable=False),
        sa.Column("timezone", sa.String, nullable=False),
        sa.Column("notification", sa.Boolean, nullable=False),
        sa.Column("notification_time", sa.Integer, nullable=False),
        sa.Column("default_extension_period", sa.BigInteger, nullable=False),
        sa.Column("default_waiting_list", sa.Boolean, nullable=False),
        sa.Column("default_public", sa.Boolean, nullable=False, default=False),
        sa.Column("default_allow_invitation", sa.Boolean, nullable=False, default=False),
        sa.Column("default_show_members", sa.Boolean, nullable=False, default=True),
        sa.Column("default_show_timezone", sa.Boolean, nullable=False, default=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
    )


def downgrade() -> None:
    op.drop_table("settings")
