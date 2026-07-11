"""Migration with tuple down_revision (merge migration — not supported)"""

revision: str = "rev2"
# A tuple with multiple entries is what alembic produces for merge migrations.
# This project explicitly bans them (build_migration_graph raises RuntimeError).
down_revision: tuple[str, ...] = ("rev1", "rev1")
