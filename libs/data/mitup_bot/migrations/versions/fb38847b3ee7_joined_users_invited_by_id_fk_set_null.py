"""joined_users invited_by_id fk set null

`joined_users.invited_by_id` was created as a bare FK to `users.id` with no on-delete action, so
deleting a user who invited someone whose join row still exists raises an FK violation. The user
cleanup batches its deletes into a single statement, so one such violation aborts the whole batch —
including privacy erasures. The FK is rebuilt with ON DELETE SET NULL: an inviter's deletion simply
detaches the `invited_by` reference on the surviving join rows.

The downgrade restores the bare FK. It is only safe once no row relies on the SET NULL behaviour;
the release contract keeps this migration reversible against the previous image.

Revision ID: fb38847b3ee7
Revises: 176d3a8a148a
Create Date: 2026-07-15 10:31:37.372183+00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb38847b3ee7"
down_revision: str | None = "176d3a8a148a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.drop_constraint("fk_joined_users_invited_by_id", "joined_users", type_="foreignkey")
    op.create_foreign_key(
        "fk_joined_users_invited_by_id", "joined_users", "users", ["invited_by_id"], ["id"], ondelete="SET NULL"
    )


def downgrade():
    op.drop_constraint("fk_joined_users_invited_by_id", "joined_users", type_="foreignkey")
    op.create_foreign_key("fk_joined_users_invited_by_id", "joined_users", "users", ["invited_by_id"], ["id"])
