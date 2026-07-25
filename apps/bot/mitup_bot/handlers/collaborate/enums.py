from enum import auto

from mitup_bot.handler_id import HandlerId

# The pairing-redemption `/start` binds ahead of the onboarding conversation (which sits at -1), so a
# confirmation link is answered rather than swallowed when the person tapping it has not finished
# setting Mitup up. Onboarding claims every non-MEMBER `/start` and knows nothing about deep-link
# payloads, so without an earlier group the payload would vanish and leave the pending row dangling.
PATREON_PAIRING_HANDLERS_GROUP = -2


class CollaborateHandlerId(HandlerId):
    SHOW = auto()
    UNLINK = auto()
    PATREON_LINK_REDEEM = auto()
    PATREON_LINK_CONFIRM = auto()
    PATREON_LINK_DECLINE = auto()
