import datetime as dt

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Message, MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.personal_filters import RichMessageFilter
from mitup_bot.handlers.utils import reply_rich_message_not_supported
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.monitoring.metric_keys import Feature, MetricKey
from mitup_bot.utils import MeetingCreationMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import build_datetime_link
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view

from ..command_enums import CommandsId
from ..main_menu.show_main_menu import callback_query_main_menu
from .enums import ConversationMeetingState, MeetingHandlerId
from .utils import active_meetings_cap_reached, main_menu_back_button, scheduling_horizon_rejection


class ValidTitleFilter(filters.MessageFilter):
    """Accept text messages that contain at most one `date_time` entity and no BOT_COMMAND."""

    def filter(self, message: Message) -> bool:
        if not message.text:
            return False
        entities = message.entities or []
        # Reject commands: BOT_COMMAND entity at offset 0 (same check as filters.COMMAND)
        if entities and entities[0].type == MessageEntity.BOT_COMMAND and entities[0].offset == 0:
            return False
        if not entities:
            return True
        return sum(e.type == MessageEntity.DATE_TIME for e in entities) <= 1


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CALLBACK, callback_data=cb.CREATE_MEETING, bindable=False
)
@with_session
async def callback_query_create_meeting(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    user = await guards.current_user(update, session)

    # Stop the common button path before the title prompt when the user is at their cap; the
    # title-message handler backstops the inline deep-link path, which never reaches here.
    if await active_meetings_cap_reached(user, update, context, back_button=main_menu_back_button(user.lang)):
        return ConversationHandler.END

    view = views.factory.create_meeting_view(
        guards.render_context(user, update, context), datetime_link=build_datetime_link()
    )

    context.store_on_exit(
        ContextId.CREATE_MEETING,
        MeetingCreationMessages.ON_EXIT.get(lang=user.lang),
        cb.CANCEL_CREATE_MEETING,
    )

    await context.api.edit_message(update=update, view=view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
    ValidTitleFilter(),
    bindable=False,
)
@with_session
async def create_meeting_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    user = await guards.current_user(update, session)

    # Backstops the inline deep-link creation path (the button path is already stopped at entry).
    if await active_meetings_cap_reached(user, update, context, back_button=main_menu_back_button(user.lang)):
        return ConversationHandler.END

    message = guards.message(update)
    title = message.text
    assert title is not None, "TEXT filter ensures this is set"

    meeting_datetime: dt.datetime | None = None

    # next() is safe: ValidTitleFilter guarantees at most one date_time entity
    date_entity = next((e for e in (message.entities or []) if e.type == MessageEntity.DATE_TIME), None)
    if date_entity is not None:
        unix_time = date_entity.unix_time
        if unix_time is not None:
            # A title-embedded date beyond the horizon keeps the user in TITLE to pick a nearer one
            # rather than silently dropping the date.
            if rejection := scheduling_horizon_rejection(user, unix_time, title_flow=True):
                await context.api.send_message(update=update, view=supporter_upsell_view(rejection, user.lang))
                return ConversationMeetingState.TITLE
            meeting_datetime = unix_time

    meetup = Meetup(
        title=title,
        owner=user,
        datetime=meeting_datetime,
        waiting_list=user.settings.default_waiting_list,
        public=user.settings.default_public,
        allow_invitation=user.settings.default_allow_invitation,
        incognito=user.settings.default_incognito,
        lock_on_start=user.settings.default_lock_on_start,
    )
    session.add(meetup)
    await session.flush()
    # A freshly flushed instance has never loaded its joined_links collection, and the async
    # engine cannot lazy-load it when the view renders below — load it explicitly.
    await session.refresh(meetup, ["joined_links"])

    success_message = MeetingCreationMessages.SUCCESS.get(title=meetup.title, lang=user.lang)
    view = meeting_views.edit_view(meetup).with_context(success_message)
    await context.api.send_message(update=update, view=view)
    context.put_feature_metric(Feature.CREATE_MEETING)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_INVALID_TITLE_MESSAGE,
    filters.TEXT & ~filters.COMMAND,
    bindable=False,
)
@with_session
async def create_meeting_invalid_title_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session)
    error_msg = MeetingCreationMessages.INVALID_TITLE_ENTITY.get(lang=user.lang)
    view = views.factory.create_meeting_view(guards.render_context(user, update, context), message=error_msg)
    await context.api.send_message(update=update, view=view)
    return ConversationMeetingState.TITLE


@HandlersRegistry.register_message(
    MeetingHandlerId.CREATE_MEETING_RICH_MESSAGE,
    RichMessageFilter(),
    bindable=False,
)
@with_session
async def create_meeting_rich_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState:
    user = await guards.current_user(update, session, load_collections=False)
    ctx = guards.render_context(user, update, context)
    view = views.factory.create_meeting_view(ctx, datetime_link=build_datetime_link())
    await reply_rich_message_not_supported(ctx, update, context, view)
    return ConversationMeetingState.TITLE


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK, callback_data=cb.CANCEL_CREATE_MEETING, bindable=False
)
async def callback_query_cancel_meeting(update: Update, context: TMitupContext) -> int:
    # Just send the user to the main menu
    await callback_query_main_menu(update, context)

    context.put_feature_metric(Feature.CREATE_MEETING, name=MetricKey.FEATURE_CANCELLED)
    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    MeetingHandlerId.CREATE_MEETING_CONVERSATION,
    entry_points_handler_names=[
        MeetingHandlerId.CREATE_MEETING_CALLBACK,
        CommandsId.START_WITH_EXISTING_USER,  # deep link from inline mode "Create a new meeting" button
    ],
    states={
        ConversationMeetingState.TITLE: [
            MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
            MeetingHandlerId.CREATE_MEETING_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[
        MeetingHandlerId.CREATE_MEETING_RICH_MESSAGE,  # rich messages get their own reply and keep the user in TITLE
        MeetingHandlerId.CREATE_MEETING_INVALID_TITLE_MESSAGE,  # must come before MESSAGE_WITHOUT_TEXT
        MessagesId.MESSAGE_WITHOUT_TEXT,
    ],
)
