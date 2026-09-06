from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot import limits
from mitup_bot.images import ImageLayout
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import ButtonMessages, Emojis, MeetingImagesMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import (
    RichContent,
    RichPhoto,
    RichTag,
    horizontal_rule_content,
    keyboard_content,
    photo_content,
    slideshow_content,
)
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views.collaborate import collaborate_button
from mitup_bot.views.factory import confirmation_view
from mitup_bot.views.meeting.banner import meeting_photos
from mitup_bot.views.mitup_view import MitupView

if TYPE_CHECKING:
    from mitup_bot.models import Meetup
    from mitup_bot.views.context import RenderContext

LAYOUT_LABELS = {
    ImageLayout.COLLAGE: ButtonMessages.COLLAGE,
    ImageLayout.SLIDESHOW: ButtonMessages.SLIDESHOW,
}


def images_heading(lang: str) -> RichContent:
    return render_rich(t"{Emojis.IMAGES} {MeetingImagesMessages.TITLE.rich(lang=lang)}").wrap(RichTag.H2)


def image_row(meeting: Meetup, position: int) -> RichContent:
    """One photo's row: its number, then the chips that replace or remove that photo.

    The label counts from one for the reader while the callback id is the stored position, which
    counts from zero.
    """
    lang = meeting.user_language
    label = MeetingImagesMessages.IMAGE_LABEL.rich(lang=lang, number=position + 1).wrap(RichTag.BOLD)
    replace = ButtonConfig(
        text=ButtonMessages.REPLACE.text(lang=lang),
        callback_data=cb.REPLACE_MEETING_IMAGE.with_ids(meeting_id=meeting.db_id, id=position),
    )
    remove = ButtonConfig(
        text=ButtonMessages.REMOVE.text(lang=lang),
        callback_data=cb.DELETE_MEETING_IMAGE.with_ids(meeting_id=meeting.db_id, id=position),
        style="danger",
    )
    return render_rich(t"{label} {replace} {remove}")


def remove_all_chip(meeting: Meetup) -> ButtonConfig:
    """Drawn as a full-width row rather than an inline chip, so it is easy to hit on a phone."""
    return ButtonConfig(
        text=ButtonMessages.REMOVE_ALL.text(lang=meeting.user_language),
        callback_data=cb.DELETE_MEETING_IMAGES.with_id(meeting.db_id),
        style="danger",
    )


def photo_list(meeting: Meetup) -> RichContent:
    """The photos as a slideshow, a numbered row under it per photo, and the chip removing all of them.

    The slideshow is used whatever layout the card holds, because it shows the photos one by one in
    the order the rows number them.
    """
    slideshow = slideshow_content([photo_content(image.file_unique_id) for image in meeting.images])
    rows = RichContent.join("\n", [image_row(meeting, position) for position in range(len(meeting.images))])
    listing = slideshow.append(rows)
    if len(meeting.images) < 2:
        return listing
    return listing.append(keyboard_content([[remove_all_chip(meeting)]]))


def layout_section(meeting: Meetup) -> RichContent:
    """The layout buttons, the current layout highlighted.

    The callback id is the layout's index in `ImageLayout`, since a callback id holds only a number.
    """
    lang = meeting.user_language
    buttons = [
        ButtonConfig(
            text=LAYOUT_LABELS[layout].text(lang=lang),
            callback_data=cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=meeting.db_id, id=index),
            style="primary" if layout is meeting.image_layout else None,
        )
        for index, layout in enumerate(ImageLayout)
    ]
    name = MeetingImagesMessages.LAYOUT.rich(lang=lang).wrap(RichTag.BOLD)
    return (
        name.append("\n")
        .append(MeetingImagesMessages.LAYOUT_EXPLANATION.rich(lang=lang))
        .append(keyboard_content([buttons]))
    )


def images_view(meeting: Meetup, *, replacing: int | None = None) -> MitupView:
    """The screen where the owner adds, replaces and removes the meeting's photos.

    *replacing* is the position of the photo the next sent photo replaces. When it is None, the
    next photo is added at the end.
    """
    lang = meeting.user_language
    lead = MeetingImagesMessages.LEAD.rich(lang=lang, limit=limits.MEETING_IMAGES_MAX)
    blocks = [images_heading(lang).append(lead)]
    if meeting.images:
        blocks.append(photo_list(meeting))
    if len(meeting.images) > 1:
        blocks.append(layout_section(meeting))
    view = MitupView(RichContent.join(horizontal_rule_content(), blocks), photos=meeting_photos(meeting))
    if replacing is not None:
        view = view.with_context(MeetingImagesMessages.REPLACING.rich(lang=lang, number=replacing + 1))
    # The back button cancels the photo flow, so a photo sent afterwards is not taken as one more
    # photo for this meeting.
    return view.with_back_button(ButtonMessages.MEETING, lang, cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id))


def images_locked_view(meeting: Meetup) -> MitupView:
    """The Images screen for an owner who is not a Host: it explains the perk and links to Collaborate.

    Remove all stays available so photos added while the owner was a Host can still be taken down.
    """
    lang = meeting.user_language
    body = images_heading(lang).append(MeetingImagesMessages.LOCKED.rich(lang=lang))
    if meeting.images:
        body = body.append(keyboard_content([[remove_all_chip(meeting)]]))
    # Nothing is waiting for a photo on this screen, so its back button is plain navigation.
    return MitupView(body, [[collaborate_button(lang)]]).with_back_button(
        ButtonMessages.MEETING, lang, cb.EDIT_MEETING.with_id(meeting.db_id)
    )


def remove_image_prompt(ctx: RenderContext, meeting: Meetup, position: int) -> MitupView:
    """The confirmation before one photo is removed, showing that photo so the owner sees what goes."""
    image = meeting.images[position]
    body = photo_content(image.file_unique_id).append(
        MeetingImagesMessages.REMOVE_CONFIRMATION.rich(lang=ctx.lang, number=position + 1)
    )
    return confirmation_view(
        ctx,
        message=body,
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_IMAGE.with_ids(meeting_id=meeting.db_id, id=position),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_IMAGE.with_ids(meeting_id=meeting.db_id, id=position),
        photos=(RichPhoto(media_id=image.file_unique_id, file_id=image.file_id),),
    )


def remove_all_images_prompt(ctx: RenderContext, meeting: Meetup) -> MitupView:
    """The confirmation before every photo is removed."""
    return confirmation_view(
        ctx,
        message=MeetingImagesMessages.REMOVE_ALL_CONFIRMATION.rich(lang=ctx.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_IMAGES.with_id(meeting.db_id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_IMAGES.with_id(meeting.db_id),
    )
