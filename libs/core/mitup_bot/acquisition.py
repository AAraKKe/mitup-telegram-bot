"""The `/start` deep-link vocabulary, and what each link says about where a user arrived from.

Telegram's only arrival signal is the payload a deep link attaches to `/start`, so the links the bot
publishes are also its attribution: each one names the surface it was published on. A `User` row
keeps that payload raw — or a sentinel, for a row some other surface created — and anyone can send
`/start <anything>`, so the stored value is user text. It reaches logs and metrics only through
`normalize_acquisition_source`, which maps it onto `AcquisitionSource`. That closed set is what makes
a per-source breakdown a bounded metric dimension rather than an open one a stranger can extend by
sending a message.

Only the kinds that name an arrival live here, and normalization is what they are for. A deep link
whose kind says nothing about where its follower came from — one only an existing user can act on —
belongs to the feature that mints it, and lands in `OTHER` on the rare occasion it reaches a new row.
"""

from enum import StrEnum

# Telegram caps a `/start` deep-link payload at 64 characters, and that payload is the widest value
# an acquisition source ever holds, so it is also the width of the column that stores one.
ACQUISITION_SOURCE_MAX_LENGTH = 64

# Deep link that drops the user straight into meeting creation, used by the inline-query surface.
INLINE_KIND = "inline"
# Reserved kind for links that name the place they were published: `src_<token>`.
CAMPAIGN_KIND = "src"

# Stamped on a row the inline Join button created, where no payload exists but the surface is known.
# The colon is outside the character set Telegram allows in a payload, which is what keeps a
# hand-typed `/start` from claiming this surface for itself.
SHARED_CARD_SOURCE = "card:shared"

ACQUISITION_SOURCE_DIMENSION = "AcquisitionSource"


class AcquisitionSource(StrEnum):
    """The arrival surfaces logs and metrics are allowed to name."""

    ORGANIC = "organic"
    """A bare `/start`: the user typed the handle, tapped Start on the bot's profile, or found the
    bot in Telegram search. Also covers hand-typed arguments that are not a well-formed payload."""
    SHARED_CARD = "shared_card"
    """The inline Join button on a meeting card shared to a group — the row was created by the join
    callback, no `/start` involved."""
    INLINE = "inline"
    """The create-your-own-meeting deep link the inline-query surface attaches to its results."""
    WEB = "web"
    """`src_web`: links on surfaces we own on the web — the docs site, its footer, the README."""
    PATREON = "patreon"
    """`src_patreon`: links we publish on the Patreon campaign page and posts."""
    FOOTER = "footer"
    """`src_footer`: reserved for the promotional footer on shared meeting cards (rich-messages
    epic); nothing emits it yet."""
    DIRECTORY = "directory"
    """`src_directory`: bot-directory listings (BotoStore, TeleHunt and the like)."""
    OTHER = "other"
    """A well-formed payload no rule recognizes: an unknown kind, an unknown `src_` token, or a link
    that names a feature rather than an arrival — the Patreon pairing link is the one of those, and
    only somebody who already has a row can follow it."""


CAMPAIGN_SOURCES = {
    "web": AcquisitionSource.WEB,
    "patreon": AcquisitionSource.PATREON,
    "footer": AcquisitionSource.FOOTER,
    "directory": AcquisitionSource.DIRECTORY,
}

LINK_KIND_SOURCES = {
    INLINE_KIND: AcquisitionSource.INLINE,
}


def normalize_acquisition_source(raw: str | None) -> AcquisitionSource:
    """Map a stored acquisition source onto the value logs and metrics may carry."""
    if raw is None:
        return AcquisitionSource.ORGANIC
    if raw == SHARED_CARD_SOURCE:
        return AcquisitionSource.SHARED_CARD

    # The same first-`_` split the deep link itself uses: the kind of link, then its token.
    kind, _, token = raw.partition("_")
    if kind == CAMPAIGN_KIND:
        return CAMPAIGN_SOURCES.get(token, AcquisitionSource.OTHER)
    return LINK_KIND_SOURCES.get(kind, AcquisitionSource.OTHER)
