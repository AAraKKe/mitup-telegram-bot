"""Pydantic models for the Patreon API responses the client consumes.

Patreon speaks JSON:API, so a response nests ``data``/``included``/``relationships`` rather than a
flat object. Modelling the slice we care about lets the client deserialize and validate in one call
instead of hand-walking dicts, and keeps the "who counts as an active patron" rule in one place.

Every model ignores unknown keys (pydantic's default), so Patreon adding fields never breaks us.
"""

from pydantic import BaseModel, Field

# Patreon marks a paying, non-lapsed patron with this status; requiring a positive entitlement on
# top of it rules out free followers and declined/expired pledges that keep the string but stop paying.
ACTIVE_PATRON_STATUS = "active_patron"


class ResourceIdentifier(BaseModel):
    """The ``{id, type}`` pointer JSON:API uses inside a relationship's ``data``."""

    id: str
    type: str | None = None


class Relationship(BaseModel):
    data: ResourceIdentifier | None = None


class MemberRelationships(BaseModel):
    campaign: Relationship | None = None
    user: Relationship | None = None


class MemberAttributes(BaseModel):
    patron_status: str | None = None
    currently_entitled_amount_cents: int = 0


class MemberResource(BaseModel):
    """A ``member`` resource, either included in an identity response or listed by the campaign sweep."""

    id: str
    type: str | None = None
    attributes: MemberAttributes = Field(default_factory=MemberAttributes)
    relationships: MemberRelationships = Field(default_factory=MemberRelationships)

    @property
    def campaign_id(self) -> str | None:
        campaign = self.relationships.campaign
        return campaign.data.id if campaign is not None and campaign.data is not None else None

    @property
    def patreon_user_id(self) -> str | None:
        """The id of the Patreon user this membership belongs to (from the ``user`` relationship)."""
        user = self.relationships.user
        return user.data.id if user is not None and user.data is not None else None

    @property
    def is_active_patron(self) -> bool:
        return (
            self.attributes.patron_status == ACTIVE_PATRON_STATUS
            and self.attributes.currently_entitled_amount_cents > 0
        )

    def is_active_patron_of(self, campaign_id: str) -> bool:
        return self.campaign_id == campaign_id and self.is_active_patron


class TokenResponse(BaseModel):
    """The OAuth token endpoint payload (both authorization-code and refresh grants)."""

    access_token: str
    refresh_token: str
    expires_in: int


class IdentityAttributes(BaseModel):
    """The ``fields[user]`` selection on ``/identity``. Everything is optional: Patreon omits an
    attribute the account has not set, and an absent display name must degrade rather than raise."""

    full_name: str | None = None


class IdentityData(BaseModel):
    id: str
    attributes: IdentityAttributes = Field(default_factory=IdentityAttributes)


class IdentityResponse(BaseModel):
    """``GET /identity`` with ``include=memberships.campaign``: the user plus their memberships."""

    data: IdentityData
    included: list[MemberResource] = Field(default_factory=list)

    @property
    def patreon_user_id(self) -> str:
        return self.data.id

    @property
    def full_name(self) -> str | None:
        """The account's display name, or None when Patreon does not report one."""
        return self.data.attributes.full_name

    def is_active_member_of(self, campaign_id: str) -> bool:
        return any(member.is_active_patron_of(campaign_id) for member in self.included)

    def entitled_amount_cents_of(self, campaign_id: str) -> int:
        """Cents currently entitled on this user's active membership of `campaign_id`, or 0 when they
        are not an active member of it. Feeds `supporter.level_for_amount` to derive the tier."""
        for member in self.included:
            if member.is_active_patron_of(campaign_id):
                return member.attributes.currently_entitled_amount_cents
        return 0


class Cursors(BaseModel):
    next: str | None = None


class Pagination(BaseModel):
    cursors: Cursors = Field(default_factory=Cursors)


class ResponseMeta(BaseModel):
    pagination: Pagination = Field(default_factory=Pagination)


class MembersResponse(BaseModel):
    """One page of ``GET /campaigns/{id}/members`` with its cursor pagination metadata."""

    data: list[MemberResource] = Field(default_factory=list)
    meta: ResponseMeta = Field(default_factory=ResponseMeta)

    @property
    def next_cursor(self) -> str | None:
        return self.meta.pagination.cursors.next


class WebhookAttributes(BaseModel):
    """The mutable state of a Patreon webhook subscription.

    ``secret`` is the HMAC key Patreon signs each delivery with. It is returned on create but only
    comes back on a read when explicitly requested via ``fields[webhook]=...secret...`` — otherwise
    it is absent, hence the optional default.
    """

    triggers: list[str] = Field(default_factory=list)
    uri: str | None = None
    paused: bool = False
    secret: str | None = None
    last_attempted_at: str | None = None
    num_consecutive_times_failed: int = 0


class WebhookResource(BaseModel):
    """A ``webhook`` resource returned by the webhook-management endpoints."""

    id: str
    type: str | None = None
    attributes: WebhookAttributes = Field(default_factory=WebhookAttributes)

    @property
    def secret(self) -> str | None:
        return self.attributes.secret


class WebhooksResponse(BaseModel):
    """``GET /webhooks``: the campaign's existing webhook subscriptions."""

    data: list[WebhookResource] = Field(default_factory=list)


class WebhookResponse(BaseModel):
    """The single-resource ``POST``/``PATCH /webhooks`` reply carrying the created/updated webhook."""

    data: WebhookResource


class WebhookMemberPayload(BaseModel):
    """The body Patreon POSTs to a ``members:*`` webhook: a single ``member`` resource in ``data``.

    We register the webhook with ``include=user&fields[member]=patron_status,currently_entitled_amount_cents``,
    so the reused :class:`MemberResource` resolves ``patreon_user_id`` from the ``user`` relationship and
    ``is_active_patron`` from the entitled amount exactly as in the daily campaign sweep. The signature is
    verified against the raw request bytes before this is parsed."""

    data: MemberResource
