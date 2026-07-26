"""The HTTP client every process uses for outbound Telegram calls."""

from telegram.request import HTTPXRequest

from mitup_bot.config import BotConfig


def build_telegram_request(config: BotConfig) -> HTTPXRequest:
    """The HTTP client for every outbound Telegram call except getUpdates, which keeps PTB's
    long-polling defaults (its read timeout is the poll interval).

    Supplying a request bypasses the defaults PTB's builder would have applied, so the pool size
    and the media write timeout are configured here rather than inherited.
    """
    return HTTPXRequest(
        connection_pool_size=config.api_connection_pool_size,
        media_write_timeout=config.api_media_write_timeout,
        connect_timeout=config.api_connect_timeout,
        read_timeout=config.api_read_timeout,
        write_timeout=config.api_write_timeout,
    )
