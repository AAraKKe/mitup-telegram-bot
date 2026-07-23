from collections.abc import Callable
from functools import partial
from typing import cast

import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, delete, false, func, literal, null, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, NotificationMessages
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import rich_title

log = structlog.get_logger(__name__)

"""Meeting permanent expiration interval. After having expired, if the meeting is still inactive, it will be deleted."""
PERMANENT_EXPIRATION_INTERVAL = "180 days"
"""Interval after expiration date to notify the owner that the meeting will be permanently deleted tomorrow."""
PERMANENT_EXPIRATION_NOTIFICATION_INTERVAL = "173 days"  # A week before the permanent expiration

MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.expiration_notification_sent == false(),
        Meetup.expiration_time != null(),
        Meetup.expiration_time + func.cast(literal(PERMANENT_EXPIRATION_NOTIFICATION_INTERVAL), INTERVAL) < func.now(),
    )
)

MEETUPS_TO_DELETE_STATEMENT: SelectOfScalar[Meetup] = select(Meetup).where(
    and_(
        Meetup.expiration_notification_sent == true(),
        Meetup.expiration_time != null(),
        Meetup.expiration_time + func.cast(literal(PERMANENT_EXPIRATION_INTERVAL), INTERVAL) < func.now(),
    )
)


async def notify_meetups_about_to_be_deleted(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    meetups = (await session.exec(MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT)).all()

    views: list[MitupView] = []
    users: list[User] = []
    callbacks: list[Callable[[User], None]] = []

    def on_success_callback(_: User, meetup_to_update: Meetup):
        log.debug("expiration notification sent", meetup=meetup_to_update.id)
        meetup_to_update.expiration_notification_sent = True

    for meetup in meetups:
        users.append(meetup.owner)
        views.append(
            MitupView(
                description=NotificationMessages.DELETION_WARNING.get(
                    lang=meetup.lang,
                    meeting_title=rich_title(meetup),
                    days_until_deletion=7,
                    past_meetings_button=ButtonMessages.PAST_MEETINGS.get(lang=meetup.user_language),
                    reactivate_meeting_button=ButtonMessages.REACTIVATE_MEETING.get(lang=meetup.user_language),
                ),
                keyboard=[
                    [
                        ButtonConfig(
                            text=ButtonMessages.REACTIVATE_MEETING.get_text(lang=meetup.user_language),
                            callback_data=cb.REACTIVATE_MEETING.with_id(cast(int, meetup.id)),
                        ),
                        ButtonConfig(
                            text=ButtonMessages.MAIN_MENU.back(lang=meetup.user_language),
                            callback_data=cb.MAIN_MENU,
                        ),
                    ]
                ],
            )
        )
        callbacks.append(partial(on_success_callback, meetup_to_update=meetup))

    if views:
        await api.send_messages_to_users(users=users, views=views, on_success=callbacks)
    metrics.emit(MetricKey.MEETUPS_ABOUT_TO_BE_DELETED, len(meetups), MetricUnit.COUNT)


async def delete_meetups(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    meetups = (await session.exec(MEETUPS_TO_DELETE_STATEMENT)).all()

    views: list[MitupView] = []
    meeting_ids: list[int] = []
    outside_user_ids: list[int] = []
    users: list[User] = []
    callbacks: list[Callable[[User], None]] = []

    def on_success_callback(_: User, meetup_to_update: Meetup):
        nonlocal meeting_ids
        # We only delete the meetings we have been able to notify the owner about
        meeting_ids.append(cast(int, meetup_to_update.id))
        outside_user_ids.extend(
            [cast(int, link.user.id) for link in meetup_to_update.joined_links if link.user.tg_user_id == -1]
        )

    for meetup in meetups:
        users.append(meetup.owner)
        views.append(
            MitupView(
                description=NotificationMessages.DELETED.get(lang=meetup.lang, meeting_title=rich_title(meetup)),
                keyboard=[],
            )
        )
        callbacks.append(partial(on_success_callback, meetup_to_update=meetup))

    if views:
        await api.send_messages_to_users(users=users, views=views, on_success=callbacks)

    await session.exec(delete(Meetup).where(col(Meetup.id).in_(meeting_ids)))
    await session.exec(delete(User).where(col(User.id).in_(outside_user_ids)))

    deleted_count = len(meeting_ids)
    failed_count = len(meetups) - deleted_count

    metrics.emit(MetricKey.MEETUPS_DELETED, deleted_count, MetricUnit.COUNT)
    metrics.emit(MetricKey.MEETINGS_DELETION_FAILED, failed_count, MetricUnit.COUNT)


@db.with_session
async def run(session: AsyncSession, api: TelegramApiWrapper, metrics: MetricsClient):
    await notify_meetups_about_to_be_deleted(session, api, metrics)
    await delete_meetups(session, api, metrics)
