"""make timestamp triggers honor explicit values

Rewrites `set_created_time` and `set_updated_time` so they only auto-fill the
column when the caller did not provide one. Required by the one-shot Rails
migration tool, which needs to preserve the original `created_at`/`updated_at`
values from the legacy DB. Existing handlers never pass these columns by hand,
so they continue to get `CURRENT_TIMESTAMP` from the fallback branch.

Revision ID: b10eabbea399
Revises: 53fa8178f227
Create Date: 2026-05-17 00:00:00.000000+00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b10eabbea399"
down_revision: str | None = "53fa8178f227"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_created_time()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.created_time IS NULL THEN
                NEW.created_time := CURRENT_TIMESTAMP;
            END IF;
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
            IF TG_OP = 'INSERT' AND NEW.updated_time IS NOT NULL THEN
                RETURN NEW;
            END IF;
            NEW.updated_time := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade():
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
