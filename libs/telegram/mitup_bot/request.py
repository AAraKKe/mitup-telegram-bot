"""The HTTP client every process uses for outbound Telegram calls."""

import re
from typing import Any

from telegram.error import TimedOut
from telegram.request import BaseRequest, HTTPXRequest, RequestData

from mitup_bot.config import BotConfig
from mitup_bot.monitoring.outbound import TELEGRAM_EDGE, outbound_call

# The Bot API method is the path segment after the `bot<token>/` prefix. Matching that prefix is
# what keeps the token out of the record: taking the last segment of an arbitrary string would
# return part of the token whenever the shape is not the expected one.
API_METHOD_PATTERN = re.compile(r"/bot[^/]+/(?P<method>[A-Za-z]+)$")
FILE_DOWNLOAD_PATTERN = re.compile(r"/file/bot[^/]+/")
UNKNOWN_API_METHOD = "unknown"
FILE_DOWNLOAD_METHOD = "downloadFile"
# The only request parameters a record may carry. Everything else — message text, markup, file
# contents — stays out of both planes.
LOGGED_REQUEST_IDS = ("chat_id", "message_id", "inline_message_id")


def api_method_from_url(url: str) -> str:
    """Name the Bot API method a request addresses, with no part of the URL reaching a record.

    A file download addresses `/file/bot<token>/<file_path>`, whose tail is a storage path rather
    than a method, so it is named as the one operation it is. Anything unrecognised is `unknown`.
    """
    if FILE_DOWNLOAD_PATTERN.search(url):
        return FILE_DOWNLOAD_METHOD
    match = API_METHOD_PATTERN.search(url)
    return match["method"] if match else UNKNOWN_API_METHOD


def request_ids(request_data: RequestData | None) -> dict[str, Any]:
    if request_data is None:
        return {}
    parameters = request_data.parameters
    return {key: parameters[key] for key in LOGGED_REQUEST_IDS if key in parameters}


class InstrumentedHTTPXRequest(HTTPXRequest):
    """An `HTTPXRequest` that records one line and one timing sample per Bot API round-trip.

    `do_request` is the sanctioned extension point — `BaseRequest.post` and `retrieve` are final —
    and it sits below the rate limiter, the custom-emoji retry and the post-commit drain, so every
    retry is recorded on its own instead of collapsing into the wrapper operation that spawned it.
    """

    def __init__(self, *args: Any, log_calls: bool = True, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.log_calls = log_calls

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        # PTB's own defaults are sentinels from a private type alias; `Any` accepts them and the
        # floats callers pass, and every value is handed straight to the base implementation.
        read_timeout: Any = BaseRequest.DEFAULT_NONE,
        write_timeout: Any = BaseRequest.DEFAULT_NONE,
        connect_timeout: Any = BaseRequest.DEFAULT_NONE,
        pool_timeout: Any = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        with outbound_call(
            TELEGRAM_EDGE,
            api_method_from_url(url),
            timeout_errors=(TimedOut,),
            log_call=self.log_calls,
            **request_ids(request_data),
        ) as call:
            status_code, payload = await super().do_request(
                url,
                method,
                request_data,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
            )
            call.status_code = status_code
        return status_code, payload


def build_telegram_request(config: BotConfig) -> InstrumentedHTTPXRequest:
    """The HTTP client for every outbound Telegram call except getUpdates, which keeps PTB's
    long-polling defaults (its read timeout is the poll interval, so timing it says nothing).

    Supplying a request bypasses the defaults PTB's builder would have applied, so the pool size
    and the media write timeout are configured here rather than inherited.
    """
    return InstrumentedHTTPXRequest(
        connection_pool_size=config.api_connection_pool_size,
        media_write_timeout=config.api_media_write_timeout,
        connect_timeout=config.api_connect_timeout,
        read_timeout=config.api_read_timeout,
        write_timeout=config.api_write_timeout,
        log_calls=config.api_call_log_enabled,
    )
