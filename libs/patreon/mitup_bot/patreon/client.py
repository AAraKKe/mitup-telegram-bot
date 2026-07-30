"""The async Patreon API client.

Every Patreon HTTP call in the bot goes through :class:`PatreonClient` — the OAuth linking flow and
the daily creator-token sweep and membership reconciliation built on top of it — so the recurring
job codes against one client rather than reaching into ``httpx`` itself. Responses are
deserialized with the pydantic models in :mod:`mitup_bot.patreon.models` and returned as-is; the
"active patron" rule lives on those models. The one value type defined here is :class:`TokenPair`,
which is not a projection of a single response: its ``expires_at`` folds the response with the clock,
and it is also the shape persisted to / re-read from the DB and passed back into :meth:`refresh`.
"""

import datetime as dt
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonApiError, PatreonTokenRevoked
from mitup_bot.monitoring.outbound import PATREON_EDGE, outbound_call, qualified_type
from mitup_bot.patreon.models import (
    IdentityResponse,
    MemberResource,
    MembersResponse,
    TokenResponse,
    WebhookResource,
    WebhookResponse,
    WebhooksResponse,
)

TOKEN_URL = "https://www.patreon.com/api/oauth2/token"
API_BASE_URL = "https://www.patreon.com/api/oauth2/v2"
# The member lifecycle events we subscribe our webhook to; a create/update/delete on a membership is
# exactly what drives supporter-tier reconciliation. The individual trigger strings are named so the
# webhook endpoint can branch on the delivered event (``X-Patreon-Event``) — a delete cancels the
# tier — without re-hardcoding the string.
MEMBER_CREATE_TRIGGER = "members:create"
MEMBER_UPDATE_TRIGGER = "members:update"
MEMBER_DELETE_TRIGGER = "members:delete"
MEMBER_WEBHOOK_TRIGGERS: tuple[str, ...] = (MEMBER_CREATE_TRIGGER, MEMBER_UPDATE_TRIGGER, MEMBER_DELETE_TRIGGER)
# The webhook fields Patreon omits unless explicitly requested — notably ``secret``, without which a
# read-back webhook could not be used to verify delivery signatures.
WEBHOOK_FIELDS = "triggers,uri,paused,secret,last_attempted_at,num_consecutive_times_failed"
# What each round-trip is recorded as. The label is passed in rather than derived from the URL:
# no instrumentation on any outbound edge records a request target or a header.
TOKEN_API_METHOD = "token"
IDENTITY_API_METHOD = "identity"
CAMPAIGN_MEMBERS_API_METHOD = "campaign_members"
WEBHOOKS_API_METHOD = "webhooks"
CREATE_WEBHOOK_API_METHOD = "create_webhook"
UPDATE_WEBHOOK_API_METHOD = "update_webhook"
# Endpoints whose response body *is* a credential. A pydantic ValidationError renders the input it
# rejected, so chaining one from these would carry the access token into every traceback that prints
# the cause; the failure is reported by its field paths instead.
CREDENTIAL_API_METHODS = frozenset({TOKEN_API_METHOD})

log = structlog.get_logger(__name__)


class TokenGrantError(StrEnum):
    """Why Patreon refused a token grant.

    ``NON_JSON_ERROR_BODY`` is its own value because a revoked credential answers 400 and Patreon
    does not always send a parseable body with it: folding it into ``UNEXPECTED_STATUS`` reports a
    credential that needs re-seeding as a transient API failure.
    """

    INVALID_GRANT = "invalid_grant"
    NON_JSON_ERROR_BODY = "non_json_error_body"
    UNEXPECTED_STATUS = "unexpected_status"


def classify_token_error(response: httpx.Response) -> TokenGrantError:
    """Read the error body of a refused token grant to tell a revoked credential from anything else."""
    if response.status_code != httpx.codes.BAD_REQUEST:
        return TokenGrantError.UNEXPECTED_STATUS
    try:
        body = response.json()
    except ValueError:
        return TokenGrantError.NON_JSON_ERROR_BODY
    if isinstance(body, dict) and body.get("error") == "invalid_grant":
        return TokenGrantError.INVALID_GRANT
    return TokenGrantError.UNEXPECTED_STATUS


def validation_error_fields(error: Exception) -> list[str]:
    """The dotted paths pydantic rejected, and only the paths.

    A validation error also renders the input it saw, which for a token response is the access token,
    so nothing derived from the values may reach a log line.
    """
    if not isinstance(error, ValidationError):
        return []
    return [".".join(str(part) for part in item["loc"]) for item in error.errors()]


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

        ``fields[user]=full_name`` is what lets the link-confirmation prompt name the account in
        words a person recognises instead of a numeric id, which is the whole basis for expecting
        someone to notice a link that is not theirs. ``full_name`` is served under the plain
        ``identity`` scope, so asking for it widens neither the consent screen nor what we can read.
        """
        response = await self._get(
            "/identity",
            access_token,
            IDENTITY_API_METHOD,
            params={
                "include": "memberships.campaign",
                "fields[user]": "full_name",
                "fields[member]": "patron_status,currently_entitled_amount_cents",
            },
        )
        return self._parse(IdentityResponse, response, IDENTITY_API_METHOD)

    async def iter_campaign_members(self, access_token: str) -> AsyncIterator[MemberResource]:
        """Yield every member of the configured campaign, following Patreon's cursor pagination.

        Driven with the creator access token by the daily reconciliation job. Each yielded
        :class:`~mitup_bot.patreon.models.MemberResource` exposes ``.patreon_user_id`` and
        ``.is_active_patron``. Pages are fetched lazily, so the caller can process members without
        holding the whole list in memory.
        """
        cursor: str | None = None
        pages = 0
        members = 0
        while True:
            params = {
                "include": "user",
                "fields[member]": "patron_status,currently_entitled_amount_cents",
            }
            if cursor is not None:
                params["page[cursor]"] = cursor
            response = await self._get(
                f"/campaigns/{self._config.campaign_id}/members",
                access_token,
                CAMPAIGN_MEMBERS_API_METHOD,
                params=params,
            )
            page = self._parse(MembersResponse, response, CAMPAIGN_MEMBERS_API_METHOD)
            pages += 1
            members += len(page.data)
            cursor = page.next_cursor
            log.info(
                "Fetched Patreon campaign member page",
                page=pages,
                members=len(page.data),
                active=sum(member.is_active_patron for member in page.data),
                has_next=cursor is not None,
            )
            for member in page.data:
                yield member
            if not cursor:
                log.info(
                    "Finished Patreon campaign member sweep",
                    pages=pages,
                    members=members,
                    reason="cursor_exhausted",
                )
                return

    async def list_webhooks(self, access_token: str) -> list[WebhookResource]:
        """List the campaign's webhooks with the creator token, requesting ``secret`` explicitly so
        each returned webhook carries the HMAC key needed to verify its deliveries."""
        response = await self._get(
            "/webhooks", access_token, WEBHOOKS_API_METHOD, params={"fields[webhook]": WEBHOOK_FIELDS}
        )
        return self._parse(WebhooksResponse, response, WEBHOOKS_API_METHOD).data

    async def create_webhook(self, access_token: str, *, uri: str, triggers: Sequence[str]) -> WebhookResource:
        """Create a webhook on the configured campaign; the returned resource carries the secret."""
        body = {
            "data": {
                "type": "webhook",
                "attributes": {"triggers": list(triggers), "uri": uri},
                "relationships": {"campaign": {"data": {"type": "campaign", "id": self._config.campaign_id}}},
            }
        }
        response = await self._post("/webhooks", access_token, CREATE_WEBHOOK_API_METHOD, json=body)
        return self._parse(WebhookResponse, response, CREATE_WEBHOOK_API_METHOD).data

    async def update_webhook(
        self,
        access_token: str,
        webhook_id: str,
        *,
        uri: str | None = None,
        triggers: Sequence[str] | None = None,
        paused: bool | None = None,
    ) -> WebhookResource:
        """PATCH only the provided attributes of an existing webhook (e.g. re-point ``uri`` or pause)."""
        attributes: dict[str, Any] = {}
        if uri is not None:
            attributes["uri"] = uri
        if triggers is not None:
            attributes["triggers"] = list(triggers)
        if paused is not None:
            attributes["paused"] = paused
        body = {"data": {"type": "webhook", "id": webhook_id, "attributes": attributes}}
        response = await self._patch(f"/webhooks/{webhook_id}", access_token, UPDATE_WEBHOOK_API_METHOD, json=body)
        return self._parse(WebhookResponse, response, UPDATE_WEBHOOK_API_METHOD).data

    async def _request_token(self, data: dict[str, str], *, revoke_on_invalid_grant: bool = False) -> TokenPair:
        grant_type = data["grant_type"]
        response = await self._send(TOKEN_API_METHOD, "POST", TOKEN_URL, data=data)
        if response.status_code != httpx.codes.OK:
            reason = classify_token_error(response)
            if revoke_on_invalid_grant and reason is TokenGrantError.INVALID_GRANT:
                log.warning(
                    "Patreon refused the token grant",
                    grant_type=grant_type,
                    status_code=response.status_code,
                    reason=str(reason),
                )
                raise PatreonTokenRevoked
            log.warning(
                "Patreon token grant failed",
                grant_type=grant_type,
                status_code=response.status_code,
                reason=str(reason),
            )
            raise PatreonApiError(f"token endpoint returned {response.status_code}")
        token = self._parse(TokenResponse, response, TOKEN_API_METHOD)
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=token.expires_in)
        log.info(
            "Patreon token grant succeeded", grant_type=grant_type, expires_in=token.expires_in, expires_at=expires_at
        )
        return TokenPair(access_token=token.access_token, refresh_token=token.refresh_token, expires_at=expires_at)

    async def _get(
        self, path: str, access_token: str, api_method: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        response = await self._send(
            api_method,
            "GET",
            f"{API_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != httpx.codes.OK:
            raise PatreonApiError(f"GET {path} returned {response.status_code}")
        return response

    async def _post(self, path: str, access_token: str, api_method: str, *, json: dict[str, Any]) -> httpx.Response:
        response = await self._send(
            api_method,
            "POST",
            f"{API_BASE_URL}{path}",
            json=json,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not response.is_success:
            raise PatreonApiError(f"POST {path} returned {response.status_code}")
        return response

    async def _patch(self, path: str, access_token: str, api_method: str, *, json: dict[str, Any]) -> httpx.Response:
        response = await self._send(
            api_method,
            "PATCH",
            f"{API_BASE_URL}{path}",
            json=json,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not response.is_success:
            raise PatreonApiError(f"PATCH {path} returned {response.status_code}")
        return response

    async def _send(self, api_method: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Every Patreon round-trip goes through here, so each one lands on a line and on the
        PatreonApi timing pair."""
        with outbound_call(PATREON_EDGE, api_method, timeout_errors=(httpx.TimeoutException,)) as call:
            response = await self._client.request(method, url, **kwargs)
            call.status_code = response.status_code
        return response

    @staticmethod
    def _parse[T: BaseModel](model: type[T], response: httpx.Response, api_method: str) -> T:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            log.error(
                "Patreon response could not be parsed",
                api_method=api_method,
                reason="schema_mismatch",
                error_type=qualified_type(error),
                error_fields=validation_error_fields(error),
                body_bytes=len(response.content),
            )
            cause = None if api_method in CREDENTIAL_API_METHODS else error
            raise PatreonApiError(f"{api_method} returned an unexpected body") from cause
