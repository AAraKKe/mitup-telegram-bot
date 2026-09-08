"""The screens a meeting is read and edited through.

One module per surface, plus the pieces they share:

- `controls`: the chips and button rows every meeting screen builds from.
- `sections`: the titled section a card is assembled out of, and its participants count line.
- `fitting`: the budget a card body is held to, and the degradation that holds it there.
- `banner`: the photo block a card opens with, and the media entries a message showing it sends.
- `owner_card`: the owner's card, where each field carries the chip that edits it.
- `attendees`: the participants section of that card.
- `shared_card`: the meeting as everyone else sees it, in the bot chat and inline, and the
  screen shown after leaving it from the bot chat.
- `list_card`: the meeting as one section of a list screen.
- `notifications`: the cards telling a participant their meeting is about to start, or has.
- `settings_card`: the meeting's own settings, and the behavior and time format sub-cards.
- `images_card`: the screen where the owner manages a meeting's photos.
- `audience`: picking the card, or the stored keyboard, for whoever is looking.
"""

__all__ = (
    "banner_content",
    "behavior_view",
    "build_inline_keyboard",
    "external_view",
    "images_locked_view",
    "images_view",
    "inline_view",
    "keyboard_for_update",
    "left_view",
    "list_heading",
    "meeting_list_section",
    "meeting_photos",
    "owner_body",
    "owner_view",
    "remove_all_images_prompt",
    "remove_image_prompt",
    "settings_view",
    "shared_body",
    "started_view",
    "starting_soon_view",
    "time_format_view",
    "view_for",
)

from .audience import keyboard_for_update, view_for
from .banner import banner_content, meeting_photos
from .images_card import images_locked_view, images_view, remove_all_images_prompt, remove_image_prompt
from .list_card import list_heading, meeting_list_section
from .notifications import started_view, starting_soon_view
from .owner_card import owner_body, owner_view
from .settings_card import behavior_view, settings_view, time_format_view
from .shared_card import build_inline_keyboard, external_view, inline_view, left_view, shared_body
