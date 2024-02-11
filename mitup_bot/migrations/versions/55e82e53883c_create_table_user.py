"""Create table user

Revision ID: 55e82e53883c
Revises:
Create Date: 2023-10-12 20:12:11.179153

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "55e82e53883c"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create table 'users'
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("created_time", sa.TIMESTAMP, nullable=False),
        sa.Column("updated_time", sa.TIMESTAMP, nullable=False),
        sa.Column("tg_user_id", sa.BigInteger, nullable=False),
        sa.Column("first_name", sa.String, nullable=False),
        sa.Column("last_name", sa.String, nullable=True),
        sa.Column("username", sa.String, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("users")
