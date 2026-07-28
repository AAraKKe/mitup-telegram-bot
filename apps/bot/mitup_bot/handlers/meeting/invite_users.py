import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, views
from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import ContextId
from mitup_bot.db import racy_flush, with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring.metric_keys import Feature, MetricKey
from mitup_bot.utils import MeetingInviteMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.factory import confirmation_view, main_menu_view
from mitup_bot.views.meeting_text import rich_title

from .enums import ConversationInviteState, MeetingHandlerId

log = structlog.get_logger(__name__)


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


def invitations_open(meeting: Meetup) -> bool:
    """Whether `meeting` still takes guests: it has a free spot and its owner still accepts additions."""
    return meeting.join_allowed() and meeting.allow_invitation


async def ensure_invitations_open(context: TMitupContext, user: User, meeting: Meetup) -> bool:
    """Report whether `meeting` still takes guests, alerting the caller with the reason when it does not.

    The two business rules are the flow's own, not a matter of access: whoever got this far may act on
    the meeting, and what stops them is the meeting being full or its guest list being closed. A
    rejection ends the flow, so the conversation state goes with it.
    """
    if invitations_open(meeting):
        return True

    message = MeetingInviteMessages.INVITES_DISABLED if meeting.join_allowed() else MeetingInviteMessages.MEETING_FULL
    await context.api.answer_callback_query(context.get_update(), text=message.get(lang=user.lang), show_alert=True)
    context.clean_user_data([ContextId.INVITE_USERS])
    return False


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

    # Cleared before the guard so a rejection cannot leave a previous attempt's meeting id behind for
    # the steps below; the ids this attempt needs are stored further down.
    context.clean_user_data([ContextId.INVITE_USERS])

    # The entry point is a tap on a shared card, so the id is client-supplied and the guard decides
    # both that the meeting is there and that the tapped message gives the caller a claim on it.
    meeting = await guards.shared_meeting(session, user, meeting_id, "invite users to a meeting", update)
    if not await ensure_invitations_open(context, user, meeting):
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
    # load_collections: the owner branch below is decided with `user.own_meeting`.
    user = await guards.current_user(update, session, load_collections=True)

    # Clean the stored data related to the conversation
    context.clean_user_data([ContextId.INVITE_USERS])

    message = MeetingInviteMessages.CANCELED.get(lang=user.lang)

    meeting_id = guards.valid_callback_data(callback_data.parse(context.match), handler_id).id
    # An optional lookup, not an access check: it only picks the screen the cancellation lands on, and
    # the owner branch below re-decides ownership. A meeting that is gone simply falls to the menu.
    meeting = await Meetup.by_id(session, meeting_id, include_inactive=False)

    if meeting is not None and invitations_open(meeting) and user.own_meeting(meeting_id):
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
        log.warning("Abandoned the invite flow", reason="empty_invited_user_name")
        context.put_feature_metric(Feature.INVITE_USERS, name=MetricKey.ERROR)
        return ConversationHandler.END

    with context.meeting_id(ContextId.INVITE_USERS, ensure_clean=False) as meeting_id:
        # flow_context: the user is typing a name in the bot chat, so a meeting that stops resolving
        # here replaces their prompt with a screen that says nothing about the invite they were in
        # the middle of. The sentence rejoins the two.
        meeting = await guards.conversation_meeting(
            session,
            user,
            meeting_id,
            "invite users to a meeting",
            flow_context=MeetingInviteMessages.FLOW_CONTEXT,
        )
        if not await ensure_invitations_open(context, user, meeting):
            # The alert says why; the user cannot continue mid conversation, so go back to the main menu
            await context.api.edit_message(
                update=update,
                view=main_menu_view(guards.render_context(user, update, context)),
            )
            return ConversationHandler.END

        context.store_text(ContextId.INVITE_USERS, invited_user_name)
        message = MeetingInviteMessages.CONFIRMATION.get(
            lang=user.lang, name=invited_user_name, meeting_title=rich_title(meeting)
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

    with context.meeting_id(ContextId.INVITE_USERS, ensure_clean=False) as authorized_meeting_id:
        if meeting_id != authorized_meeting_id:
            # The confirm button carries a client-supplied meeting id, so it is only honoured while it
            # still names the meeting authorized when this conversation was entered. Without this the
            # conversation state of any meeting could be redirected onto an arbitrary one.
            await context.api.answer_callback_query(
                update, text=MeetingInviteMessages.MEETING_NOT_FOUND.get(lang=user.lang), show_alert=True
            )
            context.clean_user_data([ContextId.INVITE_USERS])
            context.emit_metric(MetricKey.UNAUTHORIZED_MEETING_CALLBACK, include_handler_properties=False)
            return ConversationHandler.END

    with context.text(ContextId.INVITE_USERS, ensure_clean=True) as invited_user_name:
        # lock: the fullness check and the membership insert below must happen under the per-meeting
        # row lock. The earlier steps only pre-validate and must not hold it across the user's typing.
        meeting = await guards.conversation_meeting(session, user, meeting_id, "invite users to a meeting", lock=True)
        if not await ensure_invitations_open(context, user, meeting):
            # The alert says why; the user cannot continue mid conversation, so go back to the main menu
            await context.api.edit_message(
                update=update,
                view=main_menu_view(guards.render_context(user, update, context)),
            )
            return ConversationHandler.END

        # ensure_invitations_open validated join_allowed under the per-meeting row lock we still
        # hold, so add_participant cannot come back empty here — a None from racy_flush can only
        # mean the uniqueness constraint rejected a duplicate membership.
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

        message = MeetingInviteMessages.SUCCESS.get(
            lang=user.lang, name=invited_user_name, meeting_title=rich_title(meeting)
        )
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

    log.warning("Abandoned the invite flow", reason="fallback_invite_user_conversation")
    context.put_feature_metric(Feature.INVITE_USERS, name=MetricKey.ERROR)

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
