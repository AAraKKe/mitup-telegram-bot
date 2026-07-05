"""add premium subscription and patreon creator token

Adds the Patreon premium tables (`premium_subscriptions`, `patreon_creator_tokens`) plus the
`users.is_premium` flag. Token columns are stored encrypted at rest by the application-side Fernet
`EncryptedToken` type; at the DDL level they are plain text. `created_time`/`updated_time` are owned
by the shared timestamp triggers, matching every other table.

Revision ID: 966e336059a9
Revises: 02557bf55f98
Create Date: 2026-07-05 11:31:33.993205+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "966e336059a9"
down_revision: str | None = "02557bf55f98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("is_premium", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "premium_subscriptions",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("patreon_user_id", sa.String, nullable=False, unique=True),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("token_expiration", sa.DateTime, nullable=False),
        sa.Column("revoked_time", sa.DateTime, nullable=True),
        sa.Column("premium_expiration", sa.DateTime, nullable=True),
        sa.Column("expiration_notified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
    )
    helpers.add_created_time_trigger("premium_subscriptions")
    helpers.add_updated_time_trigger("premium_subscriptions")

    op.create_table(
        "patreon_creator_tokens",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=False),
        sa.Column("token_expiration", sa.DateTime, nullable=False),
        sa.Column("seed_fingerprint", sa.String, nullable=False),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
    )
    helpers.add_created_time_trigger("patreon_creator_tokens")
    helpers.add_updated_time_trigger("patreon_creator_tokens")


def downgrade():
    helpers.remove_created_time_trigger("patreon_creator_tokens")
    helpers.remove_updated_time_trigger("patreon_creator_tokens")
    op.drop_table("patreon_creator_tokens")

    helpers.remove_created_time_trigger("premium_subscriptions")
    helpers.remove_updated_time_trigger("premium_subscriptions")
    op.drop_table("premium_subscriptions")

    op.drop_column("users", "is_premium")
