from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.images import ImageLayout
from mitup_bot.utils.rich_message import (
    RichContent,
    RichPhoto,
    collage_content,
    photo_content,
    slideshow_content,
)

if TYPE_CHECKING:
    from mitup_bot.models import Meetup


def meeting_photos(meeting: Meetup) -> tuple[RichPhoto, ...]:
    """The media entries a message showing the banner sends beside its html, in banner order."""
    return tuple(RichPhoto(media_id=image.file_unique_id, file_id=image.file_id) for image in meeting.images)


def banner_content(meeting: Meetup) -> RichContent | None:
    """The meeting's photos as one block, or None when the meeting has none.

    A single photo is drawn on its own, since a collage or a slideshow needs at least two.
    """
    photos = [photo_content(image.file_unique_id) for image in meeting.images]
    if not photos:
        return None
    if len(photos) == 1:
        return photos[0]
    if meeting.image_layout is ImageLayout.COLLAGE:
        return collage_content(photos)
    return slideshow_content(photos)
