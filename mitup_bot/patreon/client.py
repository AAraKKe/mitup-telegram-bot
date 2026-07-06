"""The async Patreon API client.

Every Patreon HTTP call in the bot goes through :class:`PatreonClient` — the OAuth linking flow in
this issue and the daily creator-token sweep and membership reconciliation built on top of it — so
the recurring job codes against one client rather than reaching into ``httpx`` itself. Responses are
deserialized with the pydantic models in :mod:`mitup_bot.patreon.models` and returned as-is; the
"active patron" rule lives on those models. The one value type defined here is :class:`TokenPair`,
which is not a projection of a single response: its ``expires_at`` folds the response with the clock,
and it is also the shape persisted to / re-read from the DB and passed back into :meth:`refresh`.
"""

import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Self

import httpx
from pydantic import BaseModel, ValidationError

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonApiError, PatreonTokenRevoked
from mitup_bot.patreon.models import IdentityResponse, MemberResource, MembersResponse, TokenResponse

TOKEN_URL = "https://www.patreon.com/api/oauth2/token"
API_BASE_URL = "https://www.patreon.com/api/oauth2/v2"


@dataclass(frozen=True, slots=True)
class TokenPair:
    """An OAuth access/refresh token pair with the access token's absolute expiry (aware UTC).

    Kept as its own type (not a response projection): ``expires_at`` is derived from the response's
    relative ``expires_in`` plus the current clock, and this is the durable shape the DB stores and
    hands back into :meth:`PatreonClient.refresh`.
    """

    access_token: str
    refresh_token: str
    expires_at: dt.datetime


class PatreonClient:
    """Async Patreon API client. Use as an async context manager so the underlying HTTP client is
    closed deterministically::

        async with PatreonClient(config) as client:
            pair = await client.exchange_code(code)

    A caller-supplied ``transport`` lets tests drive every call through ``httpx.MockTransport``
    without patching the network.
    """

    def __init__(self, config: PatreonConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._client = httpx.AsyncClient(transport=transport, timeout=config.request_timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object):
        await self._client.aclose()

    async def exchange_code(self, code: str) -> TokenPair:
        """Trade an authorization ``code`` from the redirect for the user's token pair."""
        return await self._request_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret.get_secret_value(),
                "redirect_uri": self._config.redirect_uri,
            }
        )

    async def refresh(self, pair: TokenPair) -> TokenPair:
        """Exchange a refresh token for a fresh pair.

        Raises :class:`PatreonTokenRevoked` when Patreon answers ``invalid_grant`` — the user
        revoked the app on Patreon's side, which the daily job treats as a business event.
        """
        return await self._request_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": pair.refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret.get_secret_value(),
            },
            revoke_on_invalid_grant=True,
        )

    async def fetch_identity(self, access_token: str) -> IdentityResponse:
        """Fetch the user's ``/identity`` (with memberships) using their own access token (scope
        ``identity``, which already returns the viewer's membership to our own campaign).

        Returns the parsed response; the caller reads ``.patreon_user_id`` and evaluates membership
        with ``.is_active_member_of(campaign_id)`` (the campaign id lives in config, not the response).
        """
        response = await self._get(
            "/identity",
            access_token,
            params={
                "include": "memberships.campaign",
                "fields[member]": "patron_status,currently_entitled_amount_cents",
            },
        )
        return self._parse(IdentityResponse, response, "identity")

    async def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]:
        """Yield every member of the configured campaign, following Patreon's cursor pagination.

        Driven with the creator access token by the daily reconciliation job (#158). Each yielded
        :class:`~mitup_bot.patreon.models.MemberResource` exposes ``.patreon_user_id`` and
        ``.is_active_patron``. Pages are fetched lazily, so the caller can process members without
        holding the whole list in memory.
        """
        cursor: str | None = None
        while True:
            params = {
                "include": "user",
                "fields[member]": "patron_status,currently_entitled_amount_cents",
            }
            if cursor is not None:
                params["page[cursor]"] = cursor
            response = await self._get(f"/campaigns/{self._config.campaign_id}/members", access_token, params=params)
            page = self._parse(MembersResponse, response, "campaign members")
            for member in page.data:
                yield member
            cursor = page.next_cursor
            if not cursor:
                return

    async def _request_token(self, data: dict[str, str], *, revoke_on_invalid_grant: bool = False) -> TokenPair:
        response = await self._client.post(TOKEN_URL, data=data)
        is_bad_request = response.status_code == httpx.codes.BAD_REQUEST
        if revoke_on_invalid_grant and is_bad_request and self._is_invalid_grant(response):
            raise PatreonTokenRevoked
        if response.status_code != httpx.codes.OK:
            raise PatreonApiError(f"token endpoint returned {response.status_code}")
        token = self._parse(TokenResponse, response, "token endpoint")
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=token.expires_in)
        return TokenPair(access_token=token.access_token, refresh_token=token.refresh_token, expires_at=expires_at)

    async def _get(self, path: str, access_token: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        response = await self._client.get(
            f"{API_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != httpx.codes.OK:
            raise PatreonApiError(f"GET {path} returned {response.status_code}")
        return response

    @staticmethod
    def _parse[T: BaseModel](model: type[T], response: httpx.Response, context: str) -> T:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise PatreonApiError(f"{context} returned an unexpected body: {error}") from error

    @staticmethod
    def _is_invalid_grant(response: httpx.Response) -> bool:
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("error") == "invalid_grant"
