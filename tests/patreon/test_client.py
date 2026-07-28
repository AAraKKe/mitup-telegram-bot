import datetime as dt
import json
from urllib.parse import parse_qs

import httpx
import pytest
from freezegun import freeze_time
from structlog.testing import capture_logs

from mitup_bot.exceptions import PatreonApiError, PatreonTokenRevoked
from mitup_bot.patreon import PatreonClient, TokenPair
from mitup_bot.patreon.client import MEMBER_WEBHOOK_TRIGGERS
from tests.helpers import create_patreon_config

CAMPAIGN_ID = "12345"


def token_response(access: str, refresh: str, expires_in: int = 2_592_000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
            "token_type": "Bearer",
            "scope": "identity",
        },
    )


def form_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


async def test_exchange_code_returns_token_pair():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth2/token"
        form = form_of(request)
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["the-code"]
        return token_response("access-1", "refresh-1", expires_in=3600)

    config = create_patreon_config()
    with freeze_time("2026-07-05 12:00:00"):
        async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
            pair = await client.exchange_code("the-code")

        assert pair.access_token == "access-1"
        assert pair.refresh_token == "refresh-1"
        assert pair.expires_at == dt.datetime(2026, 7, 5, 13, 0, 0, tzinfo=dt.UTC)


async def test_refresh_returns_new_pair():
    def handler(request: httpx.Request) -> httpx.Response:
        form = form_of(request)
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["old-refresh"]
        return token_response("access-2", "refresh-2")

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        pair = await client.refresh(TokenPair("old-access", "old-refresh", dt.datetime.now(dt.UTC)))

    assert pair.access_token == "access-2"
    assert pair.refresh_token == "refresh-2"


async def test_refresh_raises_token_revoked_on_invalid_grant():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonTokenRevoked):
            await client.refresh(TokenPair("a", "r", dt.datetime.now(dt.UTC)))


async def test_exchange_code_maps_non_200_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonApiError):
            await client.exchange_code("code")


async def test_token_endpoint_missing_fields_maps_to_api_error():
    # 200 OK but the body is missing refresh_token/expires_in, so pydantic validation fails.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "only-access"})

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonApiError):
            await client.exchange_code("code")


async def test_refresh_non_json_400_maps_to_api_error_not_revoked():
    # A 400 whose body is not JSON is not an invalid_grant signal, so it surfaces as a generic
    # PatreonApiError rather than the business-event PatreonTokenRevoked.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="<html>Bad Request</html>")

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonApiError):
            await client.refresh(TokenPair("a", "r", dt.datetime.now(dt.UTC)))


def identity_payload(*, campaign_id: str, patron_status: str, cents: int) -> dict:
    return {
        "data": {"id": "patreon-user-9", "type": "user"},
        "included": [
            {
                "type": "member",
                "id": "member-1",
                "attributes": {"patron_status": patron_status, "currently_entitled_amount_cents": cents},
                "relationships": {"campaign": {"data": {"id": campaign_id, "type": "campaign"}}},
            }
        ],
    }


async def test_fetch_identity_reports_active_member():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth2/v2/identity"
        assert request.headers["Authorization"] == "Bearer user-access"
        return httpx.Response(
            200, json=identity_payload(campaign_id=CAMPAIGN_ID, patron_status="active_patron", cents=500)
        )

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.patreon_user_id == "patreon-user-9"
    assert identity.is_active_member_of(CAMPAIGN_ID) is True


async def test_fetch_identity_requests_and_parses_the_display_name():
    # The confirmation prompt has to name the Patreon account in words a person recognises, which is
    # the whole basis for expecting anyone to notice a link that is not theirs. `full_name` is served
    # under the plain `identity` scope, so asking for it adds no scope.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["fields[user]"] == "full_name"
        payload = identity_payload(campaign_id=CAMPAIGN_ID, patron_status="active_patron", cents=500)
        payload["data"]["attributes"] = {"full_name": "Ada Lovelace"}
        return httpx.Response(200, json=payload)

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.full_name == "Ada Lovelace"


async def test_fetch_identity_tolerates_an_account_with_no_display_name():
    # Patreon omits the attribute for accounts that never set one; the prompt falls back to the id
    # rather than the parse failing.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=identity_payload(campaign_id=CAMPAIGN_ID, patron_status="active_patron", cents=500)
        )

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.full_name is None


async def test_fetch_identity_reports_inactive_when_not_paying():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=identity_payload(campaign_id=CAMPAIGN_ID, patron_status="active_patron", cents=0)
        )

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.is_active_member_of(CAMPAIGN_ID) is False


async def test_fetch_identity_ignores_membership_of_other_campaign():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=identity_payload(campaign_id="99999", patron_status="active_patron", cents=500))

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.is_active_member_of(CAMPAIGN_ID) is False


async def test_fetch_identity_reports_entitled_amount_for_active_membership():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=identity_payload(campaign_id=CAMPAIGN_ID, patron_status="active_patron", cents=1000)
        )

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        identity = await client.fetch_identity("user-access")

    assert identity.entitled_amount_cents_of(CAMPAIGN_ID) == 1000
    # A campaign the user is not an active member of reports no entitlement, so it maps to NONE.
    assert identity.entitled_amount_cents_of("99999") == 0


async def test_iter_campaign_members_follows_cursor_pagination():
    def member(patreon_id: str, cents: int) -> dict:
        return {
            "type": "member",
            "id": f"member-{patreon_id}",
            "attributes": {"patron_status": "active_patron", "currently_entitled_amount_cents": cents},
            "relationships": {"user": {"data": {"id": patreon_id, "type": "user"}}},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/oauth2/v2/campaigns/{CAMPAIGN_ID}/members"
        cursor = request.url.params.get("page[cursor]")
        if cursor is None:
            return httpx.Response(
                200,
                json={
                    "data": [member("p1", 500), member("p2", 0)],
                    "meta": {"pagination": {"cursors": {"next": "CURSOR2"}}},
                },
            )
        assert cursor == "CURSOR2"
        return httpx.Response(200, json={"data": [member("p3", 300)], "meta": {"pagination": {"cursors": {}}}})

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        members = [member async for member in client.iter_campaign_members("creator-access")]

    assert [m.patreon_user_id for m in members] == ["p1", "p2", "p3"]
    assert [m.is_active_patron for m in members] == [True, False, True]
    assert members[0].attributes.currently_entitled_amount_cents == 500


async def test_get_maps_non_200_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=json.dumps({"errors": []}))

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonApiError):
            await client.fetch_identity("bad-token")


def webhook_payload(webhook_id: str, *, uri: str, secret: str, triggers: list[str]) -> dict:
    return {
        "type": "webhook",
        "id": webhook_id,
        "attributes": {
            "triggers": triggers,
            "uri": uri,
            "paused": False,
            "secret": secret,
            "last_attempted_at": None,
            "num_consecutive_times_failed": 0,
        },
    }


async def test_list_webhooks_returns_webhooks_including_secret():
    triggers = list(MEMBER_WEBHOOK_TRIGGERS)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth2/v2/webhooks"
        assert request.headers["Authorization"] == "Bearer creator-access"
        # The secret only comes back when requested via fields[webhook], so the client must ask for it.
        assert "secret" in request.url.params["fields[webhook]"]
        return httpx.Response(
            200,
            json={
                "data": [webhook_payload("wh-1", uri="https://bot.example/hook", secret="sh-secret", triggers=triggers)]
            },
        )

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        webhooks = await client.list_webhooks("creator-access")

    assert [webhook.id for webhook in webhooks] == ["wh-1"]
    assert webhooks[0].secret == "sh-secret"
    assert webhooks[0].attributes.triggers == triggers


async def test_create_webhook_sends_jsonapi_body_and_returns_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/oauth2/v2/webhooks"
        assert request.headers["Authorization"] == "Bearer creator-access"
        body = json.loads(request.content.decode())
        assert body == {
            "data": {
                "type": "webhook",
                "attributes": {
                    "triggers": list(MEMBER_WEBHOOK_TRIGGERS),
                    "uri": "https://bot.example/hook",
                },
                "relationships": {"campaign": {"data": {"type": "campaign", "id": CAMPAIGN_ID}}},
            }
        }
        return httpx.Response(
            201,
            json={
                "data": webhook_payload(
                    "wh-new",
                    uri="https://bot.example/hook",
                    secret="fresh-secret",
                    triggers=list(MEMBER_WEBHOOK_TRIGGERS),
                )
            },
        )

    config = create_patreon_config(campaign_id=CAMPAIGN_ID)
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        webhook = await client.create_webhook(
            "creator-access", uri="https://bot.example/hook", triggers=MEMBER_WEBHOOK_TRIGGERS
        )

    assert webhook.id == "wh-new"
    assert webhook.secret == "fresh-secret"


async def test_update_webhook_patches_only_given_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/api/oauth2/v2/webhooks/wh-1"
        body = json.loads(request.content.decode())
        # Only ``paused`` was supplied, so uri/triggers must be absent from the attributes.
        assert body == {"data": {"type": "webhook", "id": "wh-1", "attributes": {"paused": True}}}
        return httpx.Response(
            200,
            json={
                "data": webhook_payload(
                    "wh-1", uri="https://bot.example/hook", secret="s", triggers=list(MEMBER_WEBHOOK_TRIGGERS)
                )
            },
        )

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        webhook = await client.update_webhook("creator-access", "wh-1", paused=True)

    assert webhook.id == "wh-1"


async def test_create_webhook_maps_non_2xx_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    config = create_patreon_config()
    async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PatreonApiError):
            await client.create_webhook(
                "creator-access", uri="https://bot.example/hook", triggers=MEMBER_WEBHOOK_TRIGGERS
            )


async def test_every_round_trip_lands_a_line_naming_its_api_method():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"id": "u1", "type": "user", "attributes": {}}, "included": []})

    config = create_patreon_config()
    with capture_logs() as logs:
        async with PatreonClient(config, transport=httpx.MockTransport(handler)) as client:
            await client.fetch_identity("access-token")

    (line,) = [entry for entry in logs if entry["event"] == "Patreon API call"]
    assert line["api_method"] == "identity"
    assert line["status_code"] == 200
    assert line["outcome"] == "ok"
    # The access token travels in a header; no part of the request target or its headers is recorded.
    assert "access-token" not in repr(logs)
