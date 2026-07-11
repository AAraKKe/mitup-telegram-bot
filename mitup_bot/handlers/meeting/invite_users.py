from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.db import racy_flush, with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models import Meetup, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.utils import MeetingInviteMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import meeting as meeting_views
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
        update, text=MeetingInviteMessages.GO_PRIVATE.get(lang=user.lang), show_alert=True
    )


async def send_request_for_invite_name(context: TMitupContext, user: User, meeting_id: int):
    """
    Send a message to the user asking for their name to complete the invite process.
    """
    view = views.factory.request_information_with_cancel_view(
        views.RenderContext(lang=user.lang),
        message=MeetingInviteMessages.PROMPT.get(lang=user.lang),
        callback_data=cb.CANCEL_INVITE_USER.with_id(meeting_id),
    )

    await context.api.send_message_to_user(user=user, view=view)


async def ensure_meeting_still_allows_invitations(
    session: AsyncSession,
    context: TMitupContext,
    user: User,
    meeting_id: int,
    on_callback: bool = True,
    for_update: bool = False,
) -> Meetup | None:
    """
    Ensure that the meeting still allows invitations.
    If not, alert the user and return None.

    The confirm step passes `for_update=True` so the fullness check and the membership insert
    happen under the per-meeting row lock; the earlier conversation steps only pre-validate and
    must not hold the lock across the user's typing.
    """
    meeting = await Meetup.by_id(session, meeting_id, include_inactive=False, for_update=for_update)
    update = context.get_update()

    if meeting is None:
        message = MeetingInviteMessages.MEETING_NOT_FOUND if on_callback else MeetingInviteMessages.MEETING_LOST_RETRY

        await context.api.answer_callback_query(update, text=message.get(lang=user.lang), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    if not meeting.join_allowed():
        message = MeetingInviteMessages.MEETING_FULL
        await context.api.answer_callback_query(update, text=message.get(lang=user.lang), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    if not meeting.allow_invitation:
        message = MeetingInviteMessages.INVITES_DISABLED
        await context.api.answer_callback_query(update, text=message.get(lang=user.lang), show_alert=True)
        context.clean_user_data([ContextId.INVITE_USERS])
        return None

    return meeting


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CALLBACK, callback_data=cb.INVITE, bindable=False
)
@with_session
async def callback_query_invite_users(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationInviteState | int:
    # This action can be called by unsubscribed users
    callback_data = guards.valid_callback_data(cb.INVITE.parse(context.match), MeetingHandlerId.INVITE_USERS_CALLBACK)
    meeting_id = callback_data.id

    user = await guards.user_registered(update, session, context, MeetingInviteMessages.OPEN_CHAT)
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
        MeetingInviteMessages.ON_EXIT.get(lang=user.lang),
        cb.CANCEL_INVITE_USER.with_id(meeting_id),
    )

    return ConversationInviteState.NAME


async def abort_invitation(
    session: AsyncSession,
    update: Update,
    context: TMitupContext,
    handler_id: MeetingHandlerId,
    callback_data: CallbackData,
) -> int:
    """Abort the invite flow, returning meeting owners to their meeting and everyone else to the main menu.

    Owners are only returned to their meeting while it still allows invitations; when it is gone,
    full, or no longer accepting invitations they fall back to the main menu like everyone else.

    ``callback_data`` is the callback-data class each registration is bound to, so parsing stays in
    sync with the registration even if a call site switches to a different class.
    """
    user = await guards.current_user(update, session)

    # Clean the stored data related to the conversation
    context.clean_user_data([ContextId.INVITE_USERS])

    message = MeetingInviteMessages.CANCELED.get(lang=user.lang)

    meeting_id = guards.valid_callback_data(callback_data.parse(context.match), handler_id).id
    meeting = await ensure_meeting_still_allows_invitations(session, context, user, meeting_id)

    if meeting is not None and user.own_meeting(meeting_id):
        view = meeting_views.view_for(meeting, user).with_context(message=message)
    else:
        view = main_menu_view(guards.render_context(user, update, context), message=message)

    await context.api.edit_message(update, view)
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK, callback_data=cb.CANCEL_INVITE_USER, bindable=False
)
@with_session
async def callback_query_cancel_invite_user(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    return await abort_invitation(
        session, update, context, MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK, cb.CANCEL_INVITE_USER
    )


@HandlersRegistry.register_message(
    MeetingHandlerId.INVITE_USERS_NAME_MESSAGE,
    filters=filters.TEXT & ~filters.COMMAND,
    bindable=False,
)
@with_session
async def invite_users_name_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationInviteState | int:
    user = await guards.current_user(update, session)

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
                view=main_menu_view(guards.render_context(user, update, context)),
            )
            return ConversationHandler.END

        context.store_text(ContextId.INVITE_USERS, invited_user_name)
        message = MeetingInviteMessages.CONFIRMATION.get(
            lang=user.lang, name=invited_user_name, meeting_title=meeting.title
        )

        view = confirmation_view(
            guards.render_context(user, update, context),
            message=message,
            confirm_callback_data=cb.CONFIRM_INVITE_USER.with_id(meeting.db_id),
            decline_callback_data=cb.CANCEL_INVITE_USER.with_id(meeting.db_id),
        )

        await context.api.send_message_to_user(user, view)
        return ConversationInviteState.CONFIRMATION


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK, bindable=False, callback_data=cb.CONFIRM_INVITE_USER
)
@with_session(write=True)
async def callback_query_confirm_user_invitation(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    user = await guards.current_user(update, session)

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_INVITE_USER.parse(context.match), MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK
    )
    meeting_id = callback_data.id

    with context.text(ContextId.INVITE_USERS, ensure_clean=True) as invited_user_name:
        meeting = await ensure_meeting_still_allows_invitations(
            session, context, user, meeting_id, on_callback=False, for_update=True
        )
        if meeting is None:
            # If the user cannot continue mid conversation, go back to the main menu
            await context.api.edit_message(
                update=update,
                view=main_menu_view(guards.render_context(user, update, context)),
            )
            return ConversationHandler.END

        # ensure_meeting_still_allows_invitations validated join_allowed under the per-meeting
        # row lock we still hold, so add_participant cannot come back empty here — a None from
        # racy_flush can only mean the uniqueness constraint rejected a duplicate membership.
        invited_user = User(first_name=invited_user_name, tg_user_id=-1, status=UserStatus.JOINED_ONLY)
        joined_link = await racy_flush(
            session,
            lambda: meeting.add_participant(invited_user, invited_by=user),
            constraint=JOINED_USERS_UNIQUE_CONSTRAINT,
        )
        if joined_link is None:
            # A concurrent update already registered this participant; the joined_users unique
            # constraint rejected our duplicate. No-op: report the existing membership instead of
            # emitting a fault, leaving the transaction consistent.
            await context.api.answer_callback_query(
                update,
                text=MeetingJoinMessages.JOIN_ALREADY_JOINED.get(lang=user.lang),
                show_alert=True,
            )
            context.clean_user_data([ContextId.INVITE_USERS])
            return ConversationHandler.END

        message = MeetingInviteMessages.SUCCESS.get(lang=user.lang, name=invited_user_name, meeting_title=meeting.title)
        await context.api.edit_message(
            update=update, view=meeting_views.view_for(meeting, user).with_context(message=message)
        )

        # Clean the stored data related to the conversation
        context.clean_user_data([ContextId.INVITE_USERS])

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(
    MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK, bindable=False, callback_data=cb.CANCEL_INVITE_USER
)
@with_session
async def callback_query_decline_user_invitation(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    return await abort_invitation(
        session, update, context, MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK, cb.CANCEL_INVITE_USER
    )


@HandlersRegistry.register_callback_query(MeetingHandlerId.INVITE_USERS_FALLBACK, bindable=False)
@with_session
async def callback_query_fallback_invite_user(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    user = await guards.current_user(update, session)

    # Clean the stored data related to the conversation
    context.clean_user_data([ContextId.INVITE_USERS])

    message = MeetingInviteMessages.ADD_FAILED_RETRY.get(lang=user.lang)
    view = main_menu_view(guards.render_context(user, update, context), message=message)

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
