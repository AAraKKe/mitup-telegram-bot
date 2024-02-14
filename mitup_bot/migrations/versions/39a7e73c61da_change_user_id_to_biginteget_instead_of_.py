"""Change user id to BigInteget instead of Integer

Revision ID: 39a7e73c61da
Revises: abfe726f246e
Create Date: 2024-02-14 08:51:44.834491+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39a7e73c61da"
down_revision: str | None = "abfe726f246e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "id", existing_type=sa.Integer, type_=sa.BigInteger)


def downgrade() -> None:
    op.alter_column("users", "id", existing_type=sa.BigInteger, type_=sa.Integer)
