"""remove repeated meeting photos

Telegram refuses a card whose `<img>` blocks repeat a media id, so a meeting holding one photo twice
keeps only the copy at its lowest position. Positions are then closed up for every meeting, because
a new photo takes the photo count as its position and a gap would clash with a later row.

Revision ID: d06736ea31db
Revises: 5d732a1e811c
Create Date: 2026-10-31 12:00:00.000000+00:00

"""

from collections.abc import Sequence

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "d06736ea31db"
down_revision: str | None = "5d732a1e811c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DELETE_REPEATED_PHOTOS_SQL = """
    DELETE FROM meeting_images AS repeated
    USING meeting_images AS kept
    WHERE repeated.meetup_id = kept.meetup_id
      AND repeated.file_unique_id = kept.file_unique_id
      AND repeated.position > kept.position;
"""

# The unique constraint on (meetup_id, position) is not deferrable and is checked row by row, so
# rows first move to a negative slot, where they cannot collide with a row that has yet to move.
MOVE_TO_NEGATIVE_SLOTS_SQL = """
    UPDATE meeting_images
    SET position = -1 - ranked.slot
    FROM (
        SELECT id, ROW_NUMBER() OVER (PARTITION BY meetup_id ORDER BY position) - 1 AS slot
        FROM meeting_images
    ) AS ranked
    WHERE meeting_images.id = ranked.id
      AND ranked.slot <> meeting_images.position;
"""

SETTLE_NEGATIVE_SLOTS_SQL = """
    UPDATE meeting_images
    SET position = -1 - position
    WHERE position < 0;
"""


def upgrade():
    helpers.execute_bulk(revision, "delete_repeated_photos", DELETE_REPEATED_PHOTOS_SQL)
    helpers.execute_bulk(revision, "move_to_negative_slots", MOVE_TO_NEGATIVE_SLOTS_SQL)
    helpers.execute_bulk(revision, "settle_negative_slots", SETTLE_NEGATIVE_SLOTS_SQL)


def downgrade():
    # Deleted rows cannot be restored, and the repaired data is valid for the older code.
    pass
