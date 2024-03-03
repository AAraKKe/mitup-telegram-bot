"""Add triggers to user settings

Revision ID: d965e7c79695
Revises: 2591ff5d25c1
Create Date: 2024-03-02 21:15:30.697814+00:00

"""

from collections.abc import Sequence

import mitup_bot.migrations.helpers as helpers

# revision identifiers, used by Alembic.
revision: str = "d965e7c79695"
down_revision: str | None = "2591ff5d25c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    helpers.add_created_time_trigger("settings")
    helpers.add_updated_time_trigger("settings")


def downgrade() -> None:
    helpers.remove_created_time_trigger("settings")
    helpers.remove_updated_time_trigger("settings")
