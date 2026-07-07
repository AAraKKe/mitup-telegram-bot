"""rename premium_subscriptions to supporter_subscriptions

Renames the `premium_subscriptions` table to `supporter_subscriptions` and its
`premium_expiration` column to `support_expiration`. The timestamp triggers are named after
the table, so they are dropped and recreated under the new name.

Revision ID: 1b3befe3e1c2
Revises: 384335105b9a
Create Date: 2026-07-06 22:51:35.832641+00:00

"""

from collections.abc import Sequence

from alembic import op

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "1b3befe3e1c2"
down_revision: str | None = "384335105b9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.rename_table("premium_subscriptions", "supporter_subscriptions")
    op.alter_column("supporter_subscriptions", "premium_expiration", new_column_name="support_expiration")

    # A rename keeps the old triggers under their old names; swap them for name-matching ones.
    op.execute("DROP TRIGGER IF EXISTS premium_subscriptions_created_time_timestamp ON supporter_subscriptions")
    op.execute("DROP TRIGGER IF EXISTS premium_subscriptions_updated_time_timestamp ON supporter_subscriptions")
    helpers.add_created_time_trigger("supporter_subscriptions")
    helpers.add_updated_time_trigger("supporter_subscriptions")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS supporter_subscriptions_created_time_timestamp ON supporter_subscriptions")
    op.execute("DROP TRIGGER IF EXISTS supporter_subscriptions_updated_time_timestamp ON supporter_subscriptions")

    op.alter_column("supporter_subscriptions", "support_expiration", new_column_name="premium_expiration")
    op.rename_table("supporter_subscriptions", "premium_subscriptions")

    helpers.add_created_time_trigger("premium_subscriptions")
    helpers.add_updated_time_trigger("premium_subscriptions")
