"""Deep links back into the bot, and the username they are built from.

A card shared into someone else's chat is read by people who may never have opened the bot, so it
closes on a link that does open it. The link carries a `/start` payload naming the surface it was
tapped on, which the acquisition stamp reads to attribute the registration it leads to.

The username is adopted once at startup via `configure` (mirroring the holder pattern in
`docs_links`); the default is the production bot, so any entry point that never calls `configure`
(tests, polling dev mode) still renders a working link.
"""

DEFAULT_USERNAME = "mitupbot"

# The product name as it is written everywhere. Brand identity rather than copy: it is never
# translated and never reworded, which is why it is a constant here instead of a catalog string.
MITUP = "Mitup"


class BotLinkState:
    """Holds the bot's own username for the process. Kept on a class attribute rather than a
    module global so `configure` can replace it wholesale."""

    username: str = DEFAULT_USERNAME


def configure(username: str | None):
    """Adopt the bot's username. Called once at startup; idempotent on replace.

    None or blank (an unset key, or a present-but-empty env var) keeps the production default: a
    link to the wrong bot is worse than one to the right one from a deployment that forgot to say
    which it is.
    """
    BotLinkState.username = username.strip() if username and username.strip() else DEFAULT_USERNAME


def start_link(source: str) -> str:
    """A link that opens the bot with *source* as its `/start` payload.

    *source* has to be a bare deep-link token (base64url characters, no separator), or the
    acquisition stamp drops it as hand-typed.
    """
    return f"https://t.me/{BotLinkState.username}?start={source}"
