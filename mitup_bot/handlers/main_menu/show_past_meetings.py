from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, PaginatedMitupView, factory

from .enums import MainMenuHandlerId


async def _show_past_meetings(user: User, page_number: int, update: Update, context: TMitupContext) -> None:
    past_meetings = sorted([m for m in user.meetups if not m.active], key=lambda m: m.db_id)
    page_number = PaginatedMitupView.clamp_page(page_number, len(past_meetings))

    if buttons := [
        ButtonConfig(
            text=str(meeting.title),
            callback_data=cb.SHOW_PAST_MEETING.with_page(meeting.db_id, page_number),
        )
        for meeting in past_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingListMessages.PAST_DESCRIPTION.get(lang=user.lang),
            buttons=buttons,
            page_number=page_number,
            navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
        ).with_context_menu(
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.back(lang=user.lang),
                        callback_data=cb.MAIN_MENU,
                    )
                ]
            ]
        )
    else:
        view = factory.main_menu_view(
            lang=user.lang,
            message=MeetingListMessages.PAST_EMPTY.get(lang=user.lang),
        )

    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, callback_data=cb.PAST_MEETINGS, bindable=True
)
@with_session
async def callback_query_show_past_meetings(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    user = await guards.current_user(update, session)
    await _show_past_meetings(user, 1, update, context)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, callback_data=cb.SHOW_PAST_MEETING_PAGE, bindable=True
)
@with_session
async def callback_query_show_past_meeting_page(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    callback_data = guards.valid_callback_data(
        cb.SHOW_PAST_MEETING_PAGE.parse(context.match), MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK
    )
    user = await guards.current_user(update, session)
    await _show_past_meetings(user, callback_data.id, update, context)
