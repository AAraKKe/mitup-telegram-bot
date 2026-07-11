from enum import StrEnum


class MigrationMode(StrEnum):
    DRY_RUN = "dry-run"
    LIVE = "live"
