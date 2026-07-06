"""drop premium subscription user token columns

Drops the per-user OAuth token columns (`access_token`, `refresh_token`, `token_expiration`) from
`premium_subscriptions`. Membership now arrives via Patreon webhooks, so the callback only needs a
one-time identity fetch at link time using the exchanged access token transiently — it never
persists a refreshable per-user token, and no runtime code reads these columns anymore.

The downgrade re-adds the three columns as nullable: the dropped values were Fernet-encrypted and
cannot be reconstructed, so a NOT NULL restore is impossible; nullable is the correct reversal.

Revision ID: 5cc1a40ebdfc
Revises: b5eb46b4d886
Create Date: 2026-07-06 20:02:43.122626+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5cc1a40ebdfc"
down_revision: str | None = "b5eb46b4d886"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_column("premium_subscriptions", "token_expiration")
    op.drop_column("premium_subscriptions", "refresh_token")
    op.drop_column("premium_subscriptions", "access_token")


def downgrade():
    op.add_column("premium_subscriptions", sa.Column("access_token", sa.Text, nullable=True))
    op.add_column("premium_subscriptions", sa.Column("refresh_token", sa.Text, nullable=True))
    op.add_column("premium_subscriptions", sa.Column("token_expiration", sa.DateTime, nullable=True))
