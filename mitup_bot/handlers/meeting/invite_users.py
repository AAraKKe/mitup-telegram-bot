from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views.factory import confirmation_view, main_menu_view

from .enums import ConversationInviteState, MeetingHandlerId


async def handle_invite_from_external_chat(
    update: Update,
    context: TMitupContext,
    user: User,
    meeting_id: int,
):
    """
    Handle the case where a user clicks on an invite link from an external chat.
    Send an alerto to continue the conversation in the bot chat and send a message there.
    """
    await send_request_for_invite_name(context, user, meeting_id)
    await context.api.answer_callback_query(
        update, text=MeetingMessages.INVITE_USER_GO_PRIVATE.get(lang=user.lang, plain=True), show_alert=True
    )


async def send_request_for_invite_name(context: TMitupContext, user: User, meeting_id: int):
    """
    Send a message to the user asking for their name to complete the invite process.
    """
    view = views.factory.request_information_with_cancel_view(
        lang=user.lang,
        message=MeetingMessages.INVITE_USER_PROMPT.get(lang=user.lang),
        callback_data=cb.DECLINE_INVITE_USER.with_id(meeting_id),
    )

    await context.api.send_message_to_user(user=user, view=view)


async def ensure_meeting_still_allows_invitations(
    session: Session,
    context: TMitupContext,
    user: User,
    meeting_id: int,
    on_callback: bool = True,
) -> Meetup | None:
    """
    Ensure that the meeting still allows invitations.
    If not, send a message to the user and return False.
    """
    meeting = Meetup.by_id(session, meeting_id, include_inactive=False)
    update = context.get_update()

    if meeting is None:
        if on_callback:
            message = MeetingMessages.INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK
        else:
            message = MeetingMessages.INVITE_USERS_MEETING_NOT_FOUND

        await context.api.answer_callback_query(update, text=message.get(lang=user.lang, plain=True), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    if not meeting.join_allowed():
        message = MeetingMessages.INVITE_USER_MEETING_FULL
        await context.api.answer_callback_query(update, text=message.get(lang=user.lang, plain=True), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    if not meeting.allow_invitation:
        message = MeetingMessages.INVITE_USER_INVITES_DISABLED
        await context.api.answer_callback_query(update, text=message.get(lang=user.lang, plain=True), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    return meeting


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CALLBACK, callback_data=cb.INVITE, bindable=False
)
@with_async_session
async def callback_query_invite_users(session: Session, update: Update, context: TMitupContext):
    # This action can be called by unsubscribed users
    callback_data = guards.valid_callback_data(cb.INVITE.parse(context.match), MeetingHandlerId.INVITE_USERS_CALLBACK)
    meeting_id = callback_data.id

    user = await guards.user_registered(update, session, context, MeetingMessages.INVITE_USER_OPEN_CHAT)
    if user is None:
        return ConversationHandler.END

    meeting = await ensure_meeting_still_allows_invitations(session, context, user, meeting_id, on_callback=True)
    if meeting is None:
        return ConversationHandler.END

    if update.effective_chat is None:
        # We are not in a private chat with the bot
        await handle_invite_from_external_chat(update, context, user, meeting_id)
    else:
        await send_request_for_invite_name(context, user, meeting_id)

    # Keep track of the meeting id to follow up the conversation
    context.store_meeting_id(ContextId.INVITE_USERS, meeting_id)
    context.store_on_exit(
        ContextId.INVITE_USERS,
        MeetingMessages.INVITE_USER_ON_EXIT.get(lang=user.lang),
        cb.DECLINE_INVITE_USER.with_id(meeting_id),
    )

    return ConversationInviteState.NAME


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK, callback_data=cb.DECLINE_INVITE_USER, bindable=False
)
@with_async_session
async def callback_query_cancel_invite_user(session: Session, update: Update, context: TMitupContext):
    # Clear the stored meeting ID, send the user to the main menu and end the conversation
    context.clean_user_data([ContextId.INVITE_USERS])
    user = guards.current_user(update, session)
    mesage = MeetingMessages.INVITE_USERS_CANCELED.get(lang=user.lang)

    await context.api.edit_message(
        update=update,
        view=main_menu_view(lang=user.lang, message=mesage),
    )

    return ConversationHandler.END


@HandlersRegistry.register_message(
    MeetingHandlerId.INVITE_USERS_NAME_MESSAGE,
    filters=filters.TEXT & ~filters.COMMAND,
    bindable=False,
)
@with_async_session
async def message_invite_users_name(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    invited_user_name = guards.message(update).text
    if invited_user_name is None:  # pragma: no cover
        # This should not happen due to the filter applied to the handler
        context.emit_metric(MetricKey.FAULT.with_prefix("EmptyInvitedUserName"))
        return ConversationHandler.END

    with context.meeting_id(ContextId.INVITE_USERS, ensure_clean=False) as meeting_id:
        meeting = await ensure_meeting_still_allows_invitations(session, context, user, meeting_id, on_callback=False)
        if meeting is None:
            # If the user cannot continue mid conversation, go back to the main menu
            await context.api.edit_message(
                update=update,
                view=main_menu_view(lang=user.lang),
            )
            return ConversationHandler.END

        context.store_text(ContextId.INVITE_USERS, invited_user_name)
        message = MeetingMessages.INVITE_USER_CONFIRMATION.get(
            lang=user.lang, name=invited_user_name, meeting_title=meeting.title
        )

        view = confirmation_view(
            lang=user.lang,
            message=message,
            confirm_callback_data=cb.CONFIRM_INVITE_USER.with_id(meeting.db_id),
            decline_callback_data=cb.DECLINE_INVITE_USER.with_id(meeting.db_id),
        )

        await context.api.send_message_to_user(user, view)
        return ConversationInviteState.CONFIRMATION


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK, bindable=False, callback_data=cb.CONFIRM_INVITE_USER
)
@with_async_session
async def confirm_user_invitation(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_INVITE_USER.parse(context.match), MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK
    )
    meeting_id = callback_data.id

    with context.text(ContextId.INVITE_USERS, ensure_clean=True) as invited_user_name:
        meeting = await ensure_meeting_still_allows_invitations(session, context, user, meeting_id, on_callback=False)
        if meeting is None:
            # If the user cannot continue mid conversation, go back to the main menu
            await context.api.edit_message(
                update=update,
                view=main_menu_view(lang=user.lang),
            )
            return ConversationHandler.END

        invited_user = User(first_name=invited_user_name, tg_user_id=-1, is_active=False)
        joined_link = meeting.add_participant(invited_user, invited_by=user)

        if joined_link is None:  # pragma: no cover
            # This is unexpected since we have already validated this before but there might be a race condition
            # Lets emit a fault metric and inform the user
            context.emit_metric(MetricKey.FAULT.with_prefix("InviteUserMeetingFullOnConfirm"))
            message = MeetingMessages.INVITE_USER_MEETING_FULL.get(
                lang=user.lang, name=invited_user_name, meeting_title=meeting.title
            )
        else:
            session.add(joined_link)
            session.add(invited_user)

            message = MeetingMessages.INVITE_USER_SUCCESS.get(
                lang=user.lang, name=invited_user_name, meeting_title=meeting.title
            )

        await context.api.edit_message(
            update=update,
            view=meeting.view_for(user).with_context(message=message),
        )

        # Clean the stored data related to the conversation
        context.clean_user_data([ContextId.INVITE_USERS])

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK, bindable=False, callback_data=cb.DECLINE_INVITE_USER
)
@with_async_session
async def decline_user_invitation(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    # Clean the stored data related to the conversation
    context.clean_user_data([ContextId.INVITE_USERS])

    message = MeetingMessages.INVITE_USERS_CANCELED.get(lang=user.lang)

    # If the user owns the meeting, go back to the meeting, if they do not
    # send to main menu
    meeting_id = guards.valid_callback_data(
        cb.DECLINE_INVITE_USER.parse(context.match), MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK
    ).id
    meeting = await ensure_meeting_still_allows_invitations(session, context, user, meeting_id)
    if meeting is None:
        # Edit message to send to the main menu
        view = main_menu_view(lang=user.lang, message=message)
        await context.api.edit_message(update, view)
        return ConversationHandler.END

    if user.own_meeting(meeting_id):
        view = meeting.view_for(user).with_context(message=message)
    else:
        view = main_menu_view(lang=user.lang, message=message)

    await context.api.edit_message(update, view)
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(MeetingHandlerId.INVITE_USERS_FALLBACK, bindable=False)
@with_async_session
async def fallback_invite_user(session: Session, update: Update, context: TMitupContext):
    user = guards.current_user(update, session)

    # Clean the stored data related to the conversation
    context.clean_user_data([ContextId.INVITE_USERS])

    message = MeetingMessages.INVITE_USERS_UNEXPECTED_UPDATES.get(lang=user.lang)
    view = main_menu_view(lang=user.lang, message=message)

    await context.api.send_message_to_user(user, view)

    context.emit_metric(MetricKey.FAULT.with_prefix("FallbackInviteUserConversation"))

    return ConversationHandler.END


HandlersRegistry.register_conversation_handler(
    MeetingHandlerId.INVITE_USERS_CONVERSATION,
    entry_points_handler_names=[MeetingHandlerId.INVITE_USERS_CALLBACK],
    states={
        ConversationInviteState.NAME: [
            MeetingHandlerId.INVITE_USERS_NAME_MESSAGE,
            MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK,
        ],
        ConversationInviteState.CONFIRMATION: [
            MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK,
            MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK,
        ],
    },
    fallbacks=[MeetingHandlerId.INVITE_USERS_FALLBACK],
    per_chat=False,
)
