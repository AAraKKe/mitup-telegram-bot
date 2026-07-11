"""add patreon webhook table

Adds `patreon_webhooks`, the single-row table that persists the registered Patreon webhook (its
Patreon-side id, receiving uri, and HMAC signing secret). The secret is stored encrypted at rest by
the application-side Fernet `EncryptedToken` type; at the DDL level it is plain text. The table ships
empty — no runtime code populates or reads it yet. `created_time`/`updated_time` are owned by the
shared timestamp triggers, matching every other table.

Revision ID: b5eb46b4d886
Revises: c459065f341a
Create Date: 2026-07-06 19:47:13.464499+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "b5eb46b4d886"
down_revision: str | None = "c459065f341a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "patreon_webhooks",
        sa.Column("id", sa.Integer, nullable=False, primary_key=True),
        sa.Column("patreon_webhook_id", sa.String, nullable=False, unique=True),
        sa.Column("uri", sa.String, nullable=False),
        sa.Column("secret", sa.Text, nullable=False),
        sa.Column("created_time", sa.DateTime, nullable=True),
        sa.Column("updated_time", sa.DateTime, nullable=True),
    )
    helpers.add_created_time_trigger("patreon_webhooks")
    helpers.add_updated_time_trigger("patreon_webhooks")


def downgrade():
    helpers.remove_created_time_trigger("patreon_webhooks")
    helpers.remove_updated_time_trigger("patreon_webhooks")
    op.drop_table("patreon_webhooks")
