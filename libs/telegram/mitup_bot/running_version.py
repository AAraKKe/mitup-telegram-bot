"""The release tag this process runs, adopted once at startup via `configure` (the holder pattern of
`docs_links` and `bot_links`). It stays unknown in any process the deploy did not stamp, such as a
local run or a test."""


class RunningVersionState:
    """Holds the running release tag for the process. Kept on a class attribute rather than a
    module global so `configure` can replace it wholesale."""

    version: str | None = None


def configure(version: str | None):
    """Adopt the release tag. None or blank (an unset key, or a present-but-empty env var) leaves it
    unknown."""
    RunningVersionState.version = version.strip() if version and version.strip() else None
