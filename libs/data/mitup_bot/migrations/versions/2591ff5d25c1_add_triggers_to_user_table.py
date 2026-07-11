"""Add triggers to user table

Revision ID: 2591ff5d25c1
Revises: 65b4c46d9141
Create Date: 2024-03-02 21:09:54.285599+00:00

"""

from collections.abc import Sequence

import mitup_bot.migrations.helpers as helpers

# revision identifiers, used by Alembic.
revision: str = "2591ff5d25c1"
down_revision: str | None = "65b4c46d9141"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    helpers.add_created_time_trigger("users")
    helpers.add_updated_time_trigger("users")


def downgrade():
    helpers.remove_created_time_trigger("users")
    helpers.remove_updated_time_trigger("users")
