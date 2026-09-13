"""swap imported meeting pins to longitude first

The bot stores a meeting pin as `[longitude, latitude]`. The Rails import wrote every pin as
`[latitude, longitude]`, the old bot's order, so an imported map renders somewhere else and a pin
east of 90 degrees longitude makes Telegram reject the whole card. Imported rows keep their
original creation time, so the cutover date singles them out; no imported pin was edited after the
import, so every one of them is swapped. The update trigger moves `updated_time` on the rows it
rewrites.

Revision ID: 5a555b141e04
Revises: 55016f2b2d4e
Create Date: 2026-09-13 17:30:00.000000+00:00

"""

from collections.abc import Sequence

from mitup_bot.migrations import helpers

# revision identifiers, used by Alembic.
revision: str = "5a555b141e04"
down_revision: str | None = "55016f2b2d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The day the new bot took over: every meeting created before it came from the Rails import.
RAILS_CUTOVER_DATE = "2026-07-17"

# Swaps the two values of an imported pin. A JSON null pin has no array to swap and is skipped by
# the type check. The swap is its own inverse, so the downgrade runs the same statement.
SWAP_IMPORTED_PINS_SQL = f"""
    UPDATE meetups
    SET location = json_build_object(
        'name', location->'name',
        'coordinates', json_build_array(location->'coordinates'->1, location->'coordinates'->0))
    WHERE created_time < '{RAILS_CUTOVER_DATE}'
      AND json_typeof(location->'coordinates') = 'array';
"""


def upgrade():
    helpers.execute_bulk(revision, "swap_imported_pins", SWAP_IMPORTED_PINS_SQL)


def downgrade():
    helpers.execute_bulk(revision, "unswap_imported_pins", SWAP_IMPORTED_PINS_SQL)
