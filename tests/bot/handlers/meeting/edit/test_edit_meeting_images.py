import logging
from enum import Enum

import pytest
from telegram import Document, PhotoSize, Update
from telegram.ext import ConversationHandler

from mitup_bot import limits
from mitup_bot.api_wrapper import OutboxStrategy, meeting_job_key
from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.meeting.edit.edit_meeting_images import (
    ALBUM_HOLD_SECONDS,
    IMAGES_ACCESS_DENIED_EVENT,
    IMAGES_CHANGED_EVENT,
    IMAGES_INPUT_REJECTED_EVENT,
)
from mitup_bot.handlers.meeting.edit.enums import ConversationMeetingState, EditMeetingHandlerId
from mitup_bot.images import ImageLayout
from mitup_bot.models import MeetingImage, Meetup, User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import MeetingImagesMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.context import RenderContext
from tests.helpers import (
    HandlerContext,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    assert_locked_meetup_select,
    call_handler,
    log_record,
    make_test_metrics_client,
)
from tests.helpers.constants import DEFAULT_CHAT_ID
from tests.helpers.fixtures import create_update
from tests.helpers.stub_db import MockDbSession

THUMBNAIL = PhotoSize(file_id="thumb-file", file_unique_id="thumb-unique", width=90, height=60)
LARGEST = PhotoSize(file_id="sent-file", file_unique_id="sent-unique", width=1280, height=720)
PHOTO_MESSAGE = UpdateRequest(photo=(THUMBNAIL, LARGEST))
IMAGE_FILE_MESSAGE = UpdateRequest(
    document=Document(file_id="doc-file", file_unique_id="doc-unique", mime_type="image/png")
)
TEXT_MESSAGE = UpdateRequest(message_text="a photo of a cat, imagine it")
ALBUM_ID = "media-group-1"
ALBUM_STRATEGY = OutboxStrategy((DEFAULT_CHAT_ID, ALBUM_ID), hold_seconds=ALBUM_HOLD_SECONDS)


def with_photos(meeting: Meetup, count: int) -> Meetup:
    meeting.images = [
        MeetingImage(
            meetup_id=meeting.db_id,
            position=position,
            file_id=f"stored-file-{position}",
            file_unique_id=f"stored-unique-{position}",
        )
        for position in range(count)
    ]
    return meeting


def file_ids(meeting: Meetup) -> list[str]:
    return [image.file_id for image in meeting.images]


def seed_owner_and_meeting(mock_session: MockDbSession, owner: User, meeting: Meetup):
    mock_session.add_object(owner, "tg_user_id")
    mock_session.add_object(meeting)


async def tap_chip(
    app: StubMitupApp, handler_id: HandlerId, callback_data: CallbackData
) -> tuple[StubMitupContext, Enum | None]:
    """Press one chip, sharing the user data of *app* with the earlier calls."""
    update = create_update(UpdateRequest(callback_query=callback_data))
    handler_context = HandlerContext(update=update, app=app, metrics_client=make_test_metrics_client())
    return await call_handler(handler_id, handler_context=handler_context)


async def send_to_editor(
    app: StubMitupApp, handler_id: HandlerId, request: UpdateRequest, meeting: Meetup
) -> tuple[StubMitupContext, Enum | None]:
    """Send one message to the photo editor, sharing the user data of *app* with the earlier calls."""
    handler_context = HandlerContext(update=create_update(request), app=app, metrics_client=make_test_metrics_client())
    return await call_handler(
        handler_id, handler_context=handler_context, with_meeting_id={ContextId.EDIT_MEETING_IMAGES: meeting.db_id}
    )


@pytest.fixture
def host(user_with_settings: User) -> User:
    user_with_settings.supporter_level = SupporterLevel.HOST_1
    return user_with_settings


@pytest.fixture
def meeting_with_photos(host: User) -> Meetup:
    return with_photos(host.meetups[0], 3)


# --- Opening the screen ---


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_IMAGES.with_id(1))], indirect=True)
async def test_a_host_opening_the_screen_gets_the_editor(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.IMAGES_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)

    assert context.user_data is not None
    on_exit = context.user_data.registry[ContextId.EDIT_MEETING_IMAGES].on_exit
    assert on_exit is not None
    assert on_exit.notice is MeetingImagesMessages.ON_EXIT
    assert on_exit.cancel_callback == cb.EDIT_MEETING_CANCEL.with_id(meeting_with_photos.db_id)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_IMAGES.with_id(1))], indirect=True)
async def test_an_owner_who_is_not_a_host_gets_the_locked_screen(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    meeting = with_photos(user_with_settings.meetups[0], 2)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await call_handler(EditMeetingHandlerId.IMAGES_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting_views.images_locked_view(meeting))
    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)

    record = log_record(caplog, IMAGES_ACCESS_DENIED_EVENT)
    assert record.levelno == logging.WARNING
    assert record.__dict__["reason"] == "not_a_supporter"
    assert record.__dict__["action"] == "open"


@pytest.mark.parametrize(
    "handler_id, update",
    [
        (
            EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK,
            UpdateRequest(callback_query=cb.REPLACE_MEETING_IMAGE.with_ids(1, 0)),
        ),
        (
            EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK,
            UpdateRequest(callback_query=cb.SET_MEETING_IMAGE_LAYOUT.with_ids(1, 1)),
        ),
    ],
    ids=["replace", "set_layout"],
    indirect=["update"],
)
async def test_the_chips_that_put_photos_on_the_card_are_refused_for_an_owner_who_is_not_a_host(
    mock_session: MockDbSession,
    handler_id: EditMeetingHandlerId,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = with_photos(user_with_settings.meetups[0], 2)
    stored = file_ids(meeting)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await call_handler(handler_id, handler_context=handler_context)

    assert file_ids(meeting) == stored
    assert meeting.image_layout is ImageLayout.COLLAGE
    context.api.assert_edit_message_called(update, meeting_views.images_locked_view(meeting))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationHandler.END


@pytest.mark.parametrize(
    "handler_id, request_data",
    [
        (EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, PHOTO_MESSAGE),
        (EditMeetingHandlerId.IMAGES_DOCUMENT_MESSAGE, IMAGE_FILE_MESSAGE),
        (EditMeetingHandlerId.IMAGES_WRONG_MESSAGE, TEXT_MESSAGE),
    ],
    ids=["photo", "image_file", "other_message"],
)
async def test_every_message_to_the_editor_is_refused_for_an_owner_who_is_not_a_host(
    mock_session: MockDbSession,
    handler_id: EditMeetingHandlerId,
    request_data: UpdateRequest,
    user_with_settings: User,
    app: StubMitupApp,
):
    meeting = with_photos(user_with_settings.meetups[0], 1)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await send_to_editor(app, handler_id, request_data, meeting)

    assert len(meeting.images) == 1
    context.api.assert_send_message_called(context.get_update(), meeting_views.images_locked_view(meeting))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationHandler.END


# --- Leaving the screen ---


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(1))], indirect=True)
async def test_the_back_chip_closes_the_photo_flow(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    """The back chip is a cancel, so the owner's next photo is not added to this meeting."""
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.CANCEL,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_IMAGES: meeting_with_photos.db_id},
    )

    assert state is ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)
    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting_with_photos))


# --- Adding photos ---


async def test_a_photo_is_appended_at_the_end_of_the_banner(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    meeting = with_photos(host.meetups[0], 1)
    seed_owner_and_meeting(mock_session, host, meeting)

    context, state = await send_to_editor(app, EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, PHOTO_MESSAGE, meeting)

    assert [image.position for image in meeting.images] == [0, 1]
    assert meeting.images[1].file_id == LARGEST.file_id
    assert meeting.images[1].file_unique_id == LARGEST.file_unique_id

    context.api.assert_send_message_called(context.get_update(), meeting_views.images_view(meeting))
    context.api.assert_update_meeting_messages_called(meeting=meeting)
    assert_locked_meetup_select(mock_session)
    assert state == ConversationMeetingState.EDIT_IMAGES

    record = log_record(caplog, IMAGES_CHANGED_EVENT)
    assert record.__dict__["action"] == "appended"
    assert record.__dict__["image_count"] == 2


async def send_album(app: StubMitupApp, meeting: Meetup, photos: int) -> list[StubMitupContext]:
    """Send an album of *photos* photos the way Telegram delivers it: one message per photo, all
    with the same media group id."""
    contexts = []
    for index in range(photos):
        photo = PhotoSize(file_id=f"album-{index}", file_unique_id=f"album-unique-{index}", width=800, height=600)
        request = UpdateRequest(photo=(photo,), media_group_id=ALBUM_ID)
        context, _ = await send_to_editor(app, EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, request, meeting)
        contexts.append(context)
    return contexts


def sent_strategies(contexts: list[StubMitupContext], method: str) -> list[OutboxStrategy]:
    return [context.api.call_args(method).kwargs["strategy"] for context in contexts]


async def test_an_album_lands_one_photo_per_message(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
):
    """Telegram delivers an album as separate messages, so each one appends on its own."""
    meeting = with_photos(host.meetups[0], 0)
    seed_owner_and_meeting(mock_session, host, meeting)

    await send_album(app, meeting, 3)

    assert [image.position for image in meeting.images] == [0, 1, 2]
    assert [image.file_id for image in meeting.images] == ["album-0", "album-1", "album-2"]


async def test_an_album_answers_with_one_screen_and_draws_the_cards_once(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
):
    """Every message of the album queues its reply and its card refresh under the album, so the
    owner gets one screen and every card is redrawn once."""
    meeting = with_photos(host.meetups[0], 0)
    seed_owner_and_meeting(mock_session, host, meeting)

    contexts = await send_album(app, meeting, 3)

    assert sent_strategies(contexts, "send_message") == [ALBUM_STRATEGY] * 3
    assert (
        sent_strategies(contexts, "update_meeting_messages")
        == [OutboxStrategy(meeting_job_key(meeting.db_id), hold_seconds=ALBUM_HOLD_SECONDS)] * 3
    )


async def test_every_photo_of_an_album_shows_the_same_draft_while_the_album_arrives(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
):
    """The Images screen only comes once the album has gone quiet, so a draft keyed on the album
    shows the owner something is happening; the same key makes Telegram animate it in place."""
    meeting = with_photos(host.meetups[0], 0)
    seed_owner_and_meeting(mock_session, host, meeting)

    contexts = await send_album(app, meeting, 3)

    draft_ids = {context.api.call_args("send_draft").kwargs["draft_id"] for context in contexts}
    assert len(draft_ids) == 1
    assert draft_ids != {0}
    assert MeetingImagesMessages.ADDING_PHOTOS.rich(lang=host.lang).text in (
        contexts[0].api.call_args("send_draft").kwargs["view"].text
    )


async def test_a_photo_sent_on_its_own_is_answered_at_once(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
):
    """A photo sent alone is not part of an album, so its reply and card refresh are not delayed."""
    meeting = with_photos(host.meetups[0], 0)
    seed_owner_and_meeting(mock_session, host, meeting)

    context, _ = await send_to_editor(app, EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, PHOTO_MESSAGE, meeting)

    assert "strategy" not in context.api.call_args("send_message").kwargs
    assert "strategy" not in context.api.call_args("update_meeting_messages").kwargs
    assert context.api.call_args_list("send_draft") == []


async def test_an_album_reaching_an_owner_who_is_not_a_host_answers_once_too(
    mock_session: MockDbSession,
    user_with_settings: User,
    app: StubMitupApp,
):
    """The locked screen is also sent once per album, not once per photo."""
    meeting = with_photos(user_with_settings.meetups[0], 0)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    contexts = await send_album(app, meeting, 2)

    assert sent_strategies(contexts, "send_message") == [ALBUM_STRATEGY] * 2


async def test_a_photo_past_the_limit_is_refused_and_the_screen_says_so(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    meeting = with_photos(host.meetups[0], limits.MEETING_IMAGES_MAX)
    stored = file_ids(meeting)
    seed_owner_and_meeting(mock_session, host, meeting)

    context, state = await send_to_editor(app, EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, PHOTO_MESSAGE, meeting)

    assert file_ids(meeting) == stored
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES

    notice = MeetingImagesMessages.LIMIT_REACHED.rich(lang=host.lang, limit=limits.MEETING_IMAGES_MAX)
    assert str(limits.MEETING_IMAGES_MAX) in notice.text
    assert "${" not in notice.text
    context.api.assert_send_message_called(
        context.get_update(), meeting_views.images_view(meeting).with_context(notice)
    )

    record = log_record(caplog, IMAGES_INPUT_REJECTED_EVENT)
    assert record.__dict__["reason"] == "limit_reached"


async def test_an_image_sent_as_a_file_is_refused(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    meeting = with_photos(host.meetups[0], 1)
    stored = file_ids(meeting)
    seed_owner_and_meeting(mock_session, host, meeting)

    context, state = await send_to_editor(
        app, EditMeetingHandlerId.IMAGES_DOCUMENT_MESSAGE, IMAGE_FILE_MESSAGE, meeting
    )

    assert file_ids(meeting) == stored
    context.api.assert_update_meeting_messages_not_called()
    context.api.assert_send_message_called(
        context.get_update(),
        meeting_views.images_view(meeting).with_context(MeetingImagesMessages.SEND_AS_PHOTO.rich(lang=host.lang)),
    )
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert log_record(caplog, IMAGES_INPUT_REJECTED_EVENT).__dict__["reason"] == "sent_as_file"


async def test_a_message_that_carries_no_photo_redraws_the_screen(
    mock_session: MockDbSession,
    host: User,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    meeting = with_photos(host.meetups[0], 1)
    stored = file_ids(meeting)
    seed_owner_and_meeting(mock_session, host, meeting)

    context, state = await send_to_editor(app, EditMeetingHandlerId.IMAGES_WRONG_MESSAGE, TEXT_MESSAGE, meeting)

    assert file_ids(meeting) == stored
    context.api.assert_send_message_called(context.get_update(), meeting_views.images_view(meeting))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert log_record(caplog, IMAGES_INPUT_REJECTED_EVENT).__dict__["reason"] == "not_a_photo"


# --- Replacing a photo ---


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REPLACE_MEETING_IMAGE.with_ids(1, 1))], indirect=True
)
async def test_replace_marks_the_slot_the_next_photo_lands_in(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK, handler_context=handler_context)

    assert context.pending_position(ContextId.REPLACE_MEETING_IMAGE) == 1
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos, replacing=1))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES


async def test_the_photo_after_replace_overwrites_that_slot_alone(
    mock_session: MockDbSession,
    host: User,
    meeting_with_photos: Meetup,
    app: StubMitupApp,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    replace_update = create_update(UpdateRequest(callback_query=cb.REPLACE_MEETING_IMAGE.with_ids(1, 1)))
    await call_handler(
        EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK,
        handler_context=HandlerContext(update=replace_update, app=app, metrics_client=make_test_metrics_client()),
    )

    context, state = await send_to_editor(
        app, EditMeetingHandlerId.IMAGES_PHOTO_MESSAGE, PHOTO_MESSAGE, meeting_with_photos
    )

    assert [image.position for image in meeting_with_photos.images] == [0, 1, 2]
    assert meeting_with_photos.images[1].file_id == LARGEST.file_id
    assert meeting_with_photos.images[1].file_unique_id == LARGEST.file_unique_id
    assert meeting_with_photos.images[0].file_id == "stored-file-0"
    assert meeting_with_photos.images[2].file_id == "stored-file-2"

    assert context.pending_position(ContextId.REPLACE_MEETING_IMAGE) is None
    context.api.assert_send_message_called(context.get_update(), meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_called(meeting=meeting_with_photos)
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert log_record(caplog, IMAGES_CHANGED_EVENT).__dict__["action"] == "replaced"


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REPLACE_MEETING_IMAGE.with_ids(1, 7))], indirect=True
)
async def test_replace_on_a_slot_the_meeting_no_longer_holds_marks_nothing(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK, handler_context=handler_context)

    assert context.pending_position(ContextId.REPLACE_MEETING_IMAGE) is None
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    assert state == ConversationMeetingState.EDIT_IMAGES


async def test_a_chip_pressed_after_replace_drops_the_waiting_slot(
    mock_session: MockDbSession,
    host: User,
    meeting_with_photos: Meetup,
    app: StubMitupApp,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    await tap_chip(app, EditMeetingHandlerId.REPLACE_IMAGE_CALLBACK, cb.REPLACE_MEETING_IMAGE.with_ids(1, 2))
    context, _ = await tap_chip(
        app, EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK, cb.SET_MEETING_IMAGE_LAYOUT.with_ids(1, 1)
    )

    assert context.pending_position(ContextId.REPLACE_MEETING_IMAGE) is None


# --- Removing photos ---


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DELETE_MEETING_IMAGE.with_ids(1, 0))], indirect=True
)
async def test_remove_asks_for_confirmation_and_closes_the_photo_flow(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """The prompt replaces the editor, so a photo sent while it shows is not taken as one more."""
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.REMOVE_IMAGE_CALLBACK,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_IMAGES: meeting_with_photos.db_id},
    )

    assert file_ids(meeting_with_photos) == ["stored-file-0", "stored-file-1", "stored-file-2"]
    context.api.assert_edit_message_called(
        update, meeting_views.remove_image_prompt(RenderContext(lang=host.lang), meeting_with_photos, 0)
    )
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)
    assert log_record(caplog, "Meeting destructive action prompted").__dict__["action"] == "remove_image"


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DELETE_MEETING_IMAGE.with_ids(1, 9))], indirect=True
)
async def test_removing_a_photo_the_meeting_no_longer_holds_redraws_the_screen(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.REMOVE_IMAGE_CALLBACK, handler_context=handler_context)

    assert [image.position for image in meeting_with_photos.images] == [0, 1, 2]
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_IMAGE.with_ids(1, 0))], indirect=True
)
async def test_confirming_the_removal_closes_the_gap_the_photo_leaves(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK, handler_context=handler_context
    )

    assert [image.position for image in meeting_with_photos.images] == [0, 1]
    assert [image.file_id for image in meeting_with_photos.images] == ["stored-file-1", "stored-file-2"]
    assert [image.file_unique_id for image in meeting_with_photos.images] == ["stored-unique-1", "stored-unique-2"]

    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting_with_photos,
        current_message=meeting_with_photos.message_from_update(update),
        skip_current=True,
    )
    assert_locked_meetup_select(mock_session)
    assert state == ConversationMeetingState.EDIT_IMAGES

    record = log_record(caplog, IMAGES_CHANGED_EVENT)
    assert record.__dict__["action"] == "removed"
    assert record.__dict__["image_count"] == 2


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_IMAGE.with_ids(1, 9))], indirect=True
)
async def test_confirming_the_removal_of_a_photo_already_gone_redraws_the_screen(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK, handler_context=handler_context
    )

    assert [image.position for image in meeting_with_photos.images] == [0, 1, 2]
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_IMAGE.with_ids(1, 0))], indirect=True
)
async def test_declining_the_removal_brings_the_editor_back(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_IMAGE_CALLBACK, handler_context=handler_context
    )

    assert file_ids(meeting_with_photos) == ["stored-file-0", "stored-file-1", "stored-file-2"]
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)
    assert log_record(caplog, "Meeting destructive action declined").__dict__["action"] == "remove_image"


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING_IMAGES.with_id(1))], indirect=True)
async def test_remove_all_asks_for_confirmation_and_closes_the_photo_flow(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.REMOVE_IMAGES_CALLBACK,
        handler_context=handler_context,
        with_meeting_id={ContextId.EDIT_MEETING_IMAGES: meeting_with_photos.db_id},
    )

    assert len(meeting_with_photos.images) == 3
    context.api.assert_edit_message_called(
        update, meeting_views.remove_all_images_prompt(RenderContext(lang=host.lang), meeting_with_photos)
    )
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationHandler.END
    assert not context.has_meeting_id(ContextId.EDIT_MEETING_IMAGES)
    assert log_record(caplog, "Meeting destructive action prompted").__dict__["action"] == "remove_images"


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_IMAGES.with_id(1))], indirect=True
)
async def test_confirming_remove_all_leaves_the_card_without_photos(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGES_CALLBACK, handler_context=handler_context
    )

    assert meeting_with_photos.images == []
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting_with_photos,
        current_message=meeting_with_photos.message_from_update(update),
        skip_current=True,
    )
    assert state == ConversationMeetingState.EDIT_IMAGES

    record = log_record(caplog, IMAGES_CHANGED_EVENT)
    assert record.__dict__["action"] == "removed_all"
    assert record.__dict__["image_count"] == 0


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_IMAGES.with_id(1))], indirect=True
)
async def test_declining_remove_all_brings_the_editor_back(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_IMAGES_CALLBACK, handler_context=handler_context
    )

    assert len(meeting_with_photos.images) == 3
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_IMAGE.with_ids(1, 0))], indirect=True
)
async def test_an_owner_who_is_not_a_host_still_takes_a_photo_off_the_card(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """Photos stay on the card after the Host plan ends, so removing them must not need the plan."""
    meeting = with_photos(user_with_settings.meetups[0], 3)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGE_CALLBACK, handler_context=handler_context
    )

    assert file_ids(meeting) == ["stored-file-1", "stored-file-2"]
    context.api.assert_edit_message_called(update, meeting_views.images_locked_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting, current_message=meeting.message_from_update(update), skip_current=True
    )
    assert state == ConversationHandler.END


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_IMAGES.with_id(1))], indirect=True
)
async def test_an_owner_who_is_not_a_host_still_clears_every_photo(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = with_photos(user_with_settings.meetups[0], 3)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.CONFIRM_REMOVE_IMAGES_CALLBACK, handler_context=handler_context
    )

    assert meeting.images == []
    context.api.assert_edit_message_called(update, meeting_views.images_locked_view(meeting))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting, current_message=meeting.message_from_update(update), skip_current=True
    )
    assert state == ConversationHandler.END


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_IMAGE.with_ids(1, 0))], indirect=True
)
async def test_an_owner_who_is_not_a_host_declining_gets_the_locked_screen_back(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    meeting = with_photos(user_with_settings.meetups[0], 3)
    seed_owner_and_meeting(mock_session, user_with_settings, meeting)

    context, state = await call_handler(
        EditMeetingHandlerId.DECLINE_REMOVE_IMAGE_CALLBACK, handler_context=handler_context
    )

    assert len(meeting.images) == 3
    context.api.assert_edit_message_called(update, meeting_views.images_locked_view(meeting))
    assert state == ConversationHandler.END


# --- The layout ---


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SET_MEETING_IMAGE_LAYOUT.with_ids(1, 1))], indirect=True
)
async def test_setting_the_layout_stores_the_one_the_chip_names(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK, handler_context=handler_context)

    assert meeting_with_photos.image_layout is ImageLayout.SLIDESHOW
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_called(
        meeting=meeting_with_photos,
        current_message=meeting_with_photos.message_from_update(update),
        skip_current=True,
    )
    assert state == ConversationMeetingState.EDIT_IMAGES
    assert log_record(caplog, IMAGES_CHANGED_EVENT).__dict__["action"] == "layout_set"


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SET_MEETING_IMAGE_LAYOUT.with_ids(1, 4))], indirect=True
)
async def test_a_layout_the_bot_does_not_know_redraws_the_screen(
    mock_session: MockDbSession,
    update: Update,
    host: User,
    meeting_with_photos: Meetup,
    handler_context: HandlerContext,
):
    seed_owner_and_meeting(mock_session, host, meeting_with_photos)

    context, state = await call_handler(EditMeetingHandlerId.SET_IMAGE_LAYOUT_CALLBACK, handler_context=handler_context)

    assert meeting_with_photos.image_layout is ImageLayout.COLLAGE
    context.api.assert_edit_message_called(update, meeting_views.images_view(meeting_with_photos))
    context.api.assert_update_meeting_messages_not_called()
    assert state == ConversationMeetingState.EDIT_IMAGES
