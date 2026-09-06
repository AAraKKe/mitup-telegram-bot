"""The Images screen: where a meeting owner adds, replaces and removes the photos on the card.

How the pieces fit together:

- Two screens. A Host sees the editor (`images_view`): the current photos, a Replace and Remove
  chip per photo, Remove all, and the Collage / Slideshow selector. Any other owner sees the locked
  screen (`images_locked_view`): a notice about the Host perk, Collaborate, and Remove all if the
  meeting still holds photos. `owner_is_host` decides between them on every tap, because callback
  data can be forged. Removing photos is open to every owner; adding, replacing and the layout are
  for Hosts.

- One conversation, `EDIT_IMAGES`. Every chip on the editor is an entry point, so a tap on any of
  them (re)opens the conversation for that meeting; the meeting id is kept in
  `ContextId.EDIT_MEETING_IMAGES`. While the conversation is open, a photo message is read as a
  photo for this meeting (`images_photo_message_handler`), an image sent as a file is refused with a
  hint, and any other message is refused with the editor again. The Back chip is the shared edit
  cancel, which ends the conversation and clears the contexts.

- Remove and Remove all ask for confirmation first. The prompt replaces the editor and ends the
  conversation, so a photo sent meanwhile is not taken as one more photo; Confirm and Decline are
  entry points that redraw the editor and open the conversation again.

- Replace. Tapping Replace on photo N stores N in `ContextId.REPLACE_MEETING_IMAGE`; the next photo
  overwrites that row instead of being appended, and the slot is cleared by that photo or by any
  other chip.

- After every change, `redraw_after_change` logs it, shows the screen the owner may use, and
  refreshes every card of the meeting through `update_meeting_messages`.

- Albums. Telegram delivers an album as one update per photo. Each photo is stored on its own, but
  the reply and the card refresh are handed to the background worker under an `OutboxStrategy`
  keyed on the album and held for `ALBUM_HOLD_SECONDS`, so the owner gets one screen, with every
  photo, once the album has gone quiet.
"""

import zlib
from enum import StrEnum, auto

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import PhotoSize, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, limits, supporter
from mitup_bot.api_wrapper import OutboxStrategy, meeting_job_key
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.images import ImageLayout
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import MeetingImage, Meetup, User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import MeetingImagesMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.mitup_view import MitupView

from .enums import ConversationMeetingState, EditMeetingHandlerId

log = structlog.get_logger(__name__)

IMAGES_ACCESS_DENIED_EVENT = "Meeting photos access denied"
IMAGES_CHANGED_EVENT = "Meeting photos changed"
IMAGES_INPUT_REJECTED_EVENT = "Meeting photo input rejected"
REMOVE_IMAGE_ACTION = "remove_image"
REMOVE_IMAGES_ACTION = "remove_images"

# Telegram delivers each photo of an album as its own update and nothing marks the last one, so the
# reply waits this long for more photos before it is sent.
ALBUM_HOLD_SECONDS = 1.5


class ImageChange(StrEnum):
    APPENDED = auto()
    REPLACED = auto()
    REMOVED = auto()
    REMOVED_ALL = auto()
    LAYOUT_SET = auto()


class InputRefusal(StrEnum):
    LIMIT_REACHED = auto()
    SENT_AS_FILE = auto()
    NOT_A_PHOTO = auto()


def album_strategy(update: Update) -> OutboxStrategy | None:
    """Queue the replies to every photo of one album as a single job, so the owner gets one screen
    for the whole album. Returns None for a photo sent alone."""
    message = update.effective_message
    if message is None or message.media_group_id is None:
        return None
    return OutboxStrategy((message.chat_id, message.media_group_id), hold_seconds=ALBUM_HOLD_SECONDS)


def owner_is_host(user: User, action: str) -> bool:
    """Return whether the owner may put photos on the card, logging the refusal when not.

    Callback data is client-supplied, so every chip checks this again instead of trusting the
    screen it was tapped on.
    """
    if supporter.is_supporter(user.supporter_level):
        return True
    log.warning(IMAGES_ACCESS_DENIED_EVENT, user_id=user.db_id, action=action, reason="not_a_supporter")
    return False


async def show_images_screen(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    *,
    notice: RichContent | None = None,
    as_reply: bool = False,
) -> ConversationMeetingState:
    """Show the photo editor and store the meeting the next photo is added to.

    With *as_reply* the editor is sent as a new message instead of replacing the tapped one.
    """
    context.store_meeting_id(ContextId.EDIT_MEETING_IMAGES, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_IMAGES,
        MeetingImagesMessages.ON_EXIT,
        cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id),
        lang=meeting.user_language,
    )
    view = meeting_views.images_view(meeting, replacing=context.pending_position(ContextId.REPLACE_MEETING_IMAGE))
    if notice is not None:
        view.with_context(notice)
    if as_reply:
        await context.api.send_message(update=update, view=view, strategy=album_strategy(update))
    else:
        await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.EDIT_IMAGES


async def show_locked_screen(context: TMitupContext, update: Update, meeting: Meetup, *, as_reply: bool = False) -> int:
    """Tell an owner who is not a Host that photos need a Host plan, and end the conversation."""
    context.clean_user_data([ContextId.EDIT_MEETING_IMAGES, ContextId.REPLACE_MEETING_IMAGE])
    view = meeting_views.images_locked_view(meeting)
    if as_reply:
        await context.api.send_message(update=update, view=view, strategy=album_strategy(update))
    else:
        await context.api.edit_message(update=update, view=view)
    return ConversationHandler.END


async def redraw_after_change(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    user: User,
    change: ImageChange,
    *,
    as_reply: bool = False,
) -> ConversationMeetingState | int:
    """Log the change, show the photo screen this owner may use, and refresh every card of the meeting."""
    log.info(IMAGES_CHANGED_EVENT, user_id=user.db_id, action=change.value, image_count=len(meeting.images))

    if supporter.is_supporter(user.supporter_level):
        state = await show_images_screen(context, update, meeting, as_reply=as_reply)
    else:
        state = await show_locked_screen(context, update, meeting, as_reply=as_reply)

    # An album's cards are refreshed once, after the same wait as its reply.
    fan_out = None
    if album_strategy(update) is not None:
        fan_out = OutboxStrategy(meeting_job_key(meeting.db_id), hold_seconds=ALBUM_HOLD_SECONDS)

    if as_reply:
        await context.api.update_meeting_messages(meeting=meeting, strategy=fan_out)
    else:
        await context.api.update_meeting_messages(
            meeting=meeting,
            current_message=meeting.message_from_update(update),
            skip_current=True,
            strategy=fan_out,
        )
    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "images"})
    return state


def store_photo(meeting: Meetup, photo: PhotoSize, replacing: int | None) -> ImageChange:
    """Overwrite the photo at position *replacing*, or append *photo* when *replacing* is None."""
    if replacing is None:
        meeting.images.append(
            MeetingImage(
                meetup_id=meeting.db_id,
                position=len(meeting.images),
                file_id=photo.file_id,
                file_unique_id=photo.file_unique_id,
            )
        )
        return ImageChange.APPENDED
    image = meeting.images[replacing]
    image.file_id = photo.file_id
    image.file_unique_id = photo.file_unique_id
    return ImageChange.REPLACED


def drop_image(meeting: Meetup, position: int):
    """Remove the photo at *position* and move the ones after it down one slot.

    The rows keep their positions and take over the next row's file ids, because the unique index
    on (meetup_id, position) rejects a renumber that moves a row onto a position another row still
    holds.
    """
    for slot, following in enumerate(meeting.images[position + 1 :], start=position):
        meeting.images[slot].file_id = following.file_id
        meeting.images[slot].file_unique_id = following.file_unique_id
    meeting.images.pop()


async def reject_message(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    user: User,
    refusal: InputRefusal,
    notice: RichContent | None = None,
) -> ConversationMeetingState:
    log.warning(IMAGES_INPUT_REJECTED_EVENT, user_id=user.db_id, reason=refusal.value, image_count=len(meeting.images))
    return await show_images_screen(context, update, meeting, notice=notice, as_reply=True)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.IMAGES_CALLBACK, callback_data=cb.EDIT_MEETING_IMAGES, bindable=False
)
@with_session
async def callback_query_edit_meeting_images(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_IMAGES.parse(context.match), EditMeetingHandlerId.IMAGES_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "edit_meeting_images", context)

    if not owner_is_host(user, "open"):
        return await show_locked_screen(context, update, meeting)

    context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
    return await show_images_screen(context, update, meeting)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK, callback_data=cb.REPLACE_MEETING_IMAGE, bindable=False
)
@with_session
async def callback_query_replace_meeting_image(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_meeting_callback_data(
        cb.REPLACE_MEETING_IMAGE.parse(context.match), EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.meeting_id, "replace_meeting_image", context)

    if not owner_is_host(user, "replace"):
        return await show_locked_screen(context, update, meeting)

    context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
    if 0 <= callback_data.id < len(meeting.images):
        context.store_position(ContextId.REPLACE_MEETING_IMAGE, callback_data.id)
    return await show_images_screen(context, update, meeting)


async def show_screen_for(
    context: TMitupContext, update: Update, meeting: Meetup, user: User
) -> ConversationMeetingState | int:
    """Show the photo screen this owner may use, without any change to log."""
    if supporter.is_supporter(user.supporter_level):
        return await show_images_screen(context, update, meeting)
    return await show_locked_screen(context, update, meeting)


async def ask_confirmation(context: TMitupContext, update: Update, user: User, prompt: MitupView, action: str) -> int:
    """Replace the screen with the confirmation *prompt* and end the conversation, so a photo sent
    while the prompt shows is not taken as one for this meeting."""
    context.clean_user_data([ContextId.EDIT_MEETING_IMAGES, ContextId.REPLACE_MEETING_IMAGE])
    log.info("Meeting destructive action prompted", user_id=user.db_id, action=action)
    await context.api.edit_message(update=update, view=prompt)
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_IMAGE_CALLBACK, callback_data=cb.DELETE_MEETING_IMAGE, bindable=False
)
@with_session
async def callback_query_remove_meeting_image(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_meeting_callback_data(
        cb.DELETE_MEETING_IMAGE.parse(context.match), EditMeetingHandlerId.REMOVE_IMAGE_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.meeting_id, "remove_meeting_image", context)

    # The chip named a photo that is already gone, so the screen is redrawn as it is now.
    if not 0 <= callback_data.id < len(meeting.images):
        context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
        return await show_screen_for(context, update, meeting, user)

    prompt = meeting_views.remove_image_prompt(guards.render_context(user, update, context), meeting, callback_data.id)
    return await ask_confirmation(context, update, user, prompt, REMOVE_IMAGE_ACTION)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_IMAGE, bindable=False
)
@with_session(write=True)
async def callback_query_confirm_remove_meeting_image(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_meeting_callback_data(
        cb.CONFIRM_DELETE_MEETING_IMAGE.parse(context.match), EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session, user, callback_data.meeting_id, "confirm_remove_meeting_image", context, lock=True
    )

    context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
    if 0 <= callback_data.id < len(meeting.images):
        drop_image(meeting, callback_data.id)
        return await redraw_after_change(context, update, meeting, user, ImageChange.REMOVED)

    # The photo went away while the prompt was showing, so the screen is redrawn as it is now.
    return await show_screen_for(context, update, meeting, user)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_IMAGE_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_IMAGE, bindable=False
)
@with_session
async def callback_query_decline_remove_meeting_image(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_meeting_callback_data(
        cb.DECLINE_DELETE_MEETING_IMAGE.parse(context.match), EditMeetingHandlerId.DECLINE_REMOVE_IMAGE_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.meeting_id, "decline_remove_meeting_image", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_IMAGE_ACTION)
    return await show_screen_for(context, update, meeting, user)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.REMOVE_IMAGES_CALLBACK, callback_data=cb.DELETE_MEETING_IMAGES, bindable=False
)
@with_session
async def callback_query_remove_meeting_images(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_IMAGES.parse(context.match), EditMeetingHandlerId.REMOVE_IMAGES_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "remove_meeting_images", context)

    prompt = meeting_views.remove_all_images_prompt(guards.render_context(user, update, context), meeting)
    return await ask_confirmation(context, update, user, prompt, REMOVE_IMAGES_ACTION)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_REMOVE_IMAGES_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_IMAGES, bindable=False
)
@with_session(write=True)
async def callback_query_confirm_remove_meeting_images(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_IMAGES.parse(context.match), EditMeetingHandlerId.CONFIRM_REMOVE_IMAGES_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "confirm_remove_meeting_images", context, lock=True)

    context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
    meeting.images.clear()
    return await redraw_after_change(context, update, meeting, user, ImageChange.REMOVED_ALL)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_REMOVE_IMAGES_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_IMAGES, bindable=False
)
@with_session
async def callback_query_decline_remove_meeting_images(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_IMAGES.parse(context.match), EditMeetingHandlerId.DECLINE_REMOVE_IMAGES_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "decline_remove_meeting_images", context)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=REMOVE_IMAGES_ACTION)
    return await show_screen_for(context, update, meeting, user)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK, callback_data=cb.SET_MEETING_IMAGE_LAYOUT, bindable=False
)
@with_session(write=True)
async def callback_query_set_meeting_image_layout(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_meeting_callback_data(
        cb.SET_MEETING_IMAGE_LAYOUT.parse(context.match), EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(
        session, user, callback_data.meeting_id, "set_meeting_image_layout", context, lock=True
    )

    if not owner_is_host(user, "set_layout"):
        return await show_locked_screen(context, update, meeting)

    context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
    layouts = list(ImageLayout)
    if not 0 <= callback_data.id < len(layouts):
        return await show_images_screen(context, update, meeting)

    meeting.image_layout = layouts[callback_data.id]
    return await redraw_after_change(context, update, meeting, user, ImageChange.LAYOUT_SET)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, filters.PHOTO & ~filters.COMMAND, bindable=False
)
@with_session(write=True)
async def images_photo_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    message = guards.message(update)
    assert message.photo, "the PHOTO filter this handler is registered with guarantees the sizes"

    with context.meeting_id(ContextId.EDIT_MEETING_IMAGES, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "add_meeting_image", context, lock=True)

        if not owner_is_host(user, "send_photo"):
            return await show_locked_screen(context, update, meeting, as_reply=True)

        if message.media_group_id is not None:
            # The album's screen is sent once the album has gone quiet, so a draft shows something
            # is happening until then. The draft id only has to be the same for every photo of one
            # album and non-zero.
            draft = MeetingImagesMessages.ADDING_PHOTOS.rich(lang=user.lang)
            await context.api.send_draft(update, draft, draft_id=zlib.crc32(message.media_group_id.encode()) or 1)

        replacing = context.pending_position(ContextId.REPLACE_MEETING_IMAGE)
        context.clean_user_data([ContextId.REPLACE_MEETING_IMAGE])
        # A position past the photos the meeting holds now came from a stale screen; the photo is
        # appended instead.
        if replacing is not None and not 0 <= replacing < len(meeting.images):
            replacing = None

        if replacing is None and len(meeting.images) >= limits.MEETING_IMAGES_MAX:
            notice = MeetingImagesMessages.LIMIT_REACHED.rich(lang=user.lang, limit=limits.MEETING_IMAGES_MAX)
            return await reject_message(context, update, meeting, user, InputRefusal.LIMIT_REACHED, notice)

        # Telegram sends a photo in several sizes, smallest first; the card uses the largest.
        change = store_photo(meeting, message.photo[-1], replacing)
        return await redraw_after_change(context, update, meeting, user, change, as_reply=True)


@HandlersRegistry.register_message(EditMeetingHandlerId.IMAGES_DOCUMENT_MESSAGE, filters.Document.IMAGE, bindable=False)
@with_session
async def images_document_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_IMAGES, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "add_meeting_image_as_file", context)

        if not owner_is_host(user, "send_file"):
            return await show_locked_screen(context, update, meeting, as_reply=True)

        # An image sent as a file has no photo sizes, so the card cannot show it.
        notice = MeetingImagesMessages.SEND_AS_PHOTO.rich(lang=user.lang)
        return await reject_message(context, update, meeting, user, InputRefusal.SENT_AS_FILE, notice)


@HandlersRegistry.register_message(EditMeetingHandlerId.IMAGES_WRONG_MESSAGE, ~filters.COMMAND, bindable=False)
@with_session
async def images_wrong_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_IMAGES, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "wrong_meeting_image_input", context)

        if not owner_is_host(user, "wrong_input"):
            return await show_locked_screen(context, update, meeting, as_reply=True)

        return await reject_message(context, update, meeting, user, InputRefusal.NOT_A_PHOTO)


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.IMAGES_CONVERSATION,
    entry_points_handler_names=[
        # The screen stays on after the conversation ends, so every chip is an entry point: a tap on
        # any of them starts the flow again and the next photo goes to this meeting.
        EditMeetingHandlerId.IMAGES_CALLBACK,
        EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK,
        EditMeetingHandlerId.REMOVE_IMAGE_CALLBACK,
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK,
        EditMeetingHandlerId.DECLINE_REMOVE_IMAGE_CALLBACK,
        EditMeetingHandlerId.REMOVE_IMAGES_CALLBACK,
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGES_CALLBACK,
        EditMeetingHandlerId.DECLINE_REMOVE_IMAGES_CALLBACK,
        EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK,
    ],
    states={
        ConversationMeetingState.EDIT_IMAGES: [
            EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE,
            EditMeetingHandlerId.IMAGES_DOCUMENT_MESSAGE,
            EditMeetingHandlerId.CANCEL,
            EditMeetingHandlerId.IMAGES_WRONG_MESSAGE,
        ],
    },
    fallbacks=[MessagesId.MESSAGE_WITHOUT_TEXT],
)
