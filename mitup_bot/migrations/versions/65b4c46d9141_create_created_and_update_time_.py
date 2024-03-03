"""Create created and update time procedures

Revision ID: 65b4c46d9141
Revises: 39a7e73c61da
Create Date: 2024-03-02 21:05:29.826742+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "65b4c46d9141"
down_revision: str | None = "39a7e73c61da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_created_time()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.created_time := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_time()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_time := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_created_time()")
    op.execute("DROP FUNCTION IF EXISTS set_updated_time()")
