"""Single source of truth for links into the docs site.

The docs host differs per deployed environment (staging vs prod), so views resolve every docs URL
through this module instead of holding literals. The base URL is adopted once at startup via
`configure` (mirroring the holder pattern in `supporter`); the default is the production site, so
any entry point that never calls `configure` — tests, polling dev mode — still renders working
links.
"""

BOT_DOMAIN_PREFIX = "bot."
DEFAULT_BASE_URL = "https://mitup.social"
USER_GUIDE_PATH = "/user-guide/"
PRIVACY_PATH = "/faq/privacy/"
COLLABORATE_PATH = "/collaborate/donation/"
LIMITS_PATH = "/user-guide/limits/"


class DocsState:
    """Holds the runtime-resolved docs base URL. Kept on a class attribute rather than a module
    global so `configure` can replace it wholesale; defaults to the production docs site."""

    base_url: str = DEFAULT_BASE_URL


def configure(bot_domain: str | None):
    """Adopt the docs base URL derived from the bot's configured domain. Called once at startup;
    idempotent on replace."""
    DocsState.base_url = base_url_for_domain(bot_domain)


def base_url_for_domain(bot_domain: str | None) -> str:
    """Derive the docs base URL from the bot's public domain.

    Infra provisions the bot host as `bot.<docs-domain>` in every deployed environment, so the
    docs host is the bot domain with its leading `bot.` label stripped. A domain without that
    prefix is used as-is; None or blank (polling mode, or a present-but-empty env var) keeps
    the production default.
    """
    if bot_domain is None or not bot_domain.strip():
        return DEFAULT_BASE_URL
    return f"https://{bot_domain.removeprefix(BOT_DOMAIN_PREFIX)}"


def user_guide_url() -> str:
    return f"{DocsState.base_url}{USER_GUIDE_PATH}"


def privacy_url() -> str:
    return f"{DocsState.base_url}{PRIVACY_PATH}"


def collaborate_url() -> str:
    return f"{DocsState.base_url}{COLLABORATE_PATH}"


def limits_url() -> str:
    return f"{DocsState.base_url}{LIMITS_PATH}"
