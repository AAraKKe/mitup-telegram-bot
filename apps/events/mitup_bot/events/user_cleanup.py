import structlog
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, delete, exists, false, func, literal, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import JoinedUsers, Meetup, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.patreon import pending_links
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView

log = structlog.get_logger(__name__)

INACTIVE_USERS_SELECT_STATEMENT: SelectOfScalar[int] | SelectOfScalar[None] = select(User.id).where(
    and_(
        User.status == UserStatus.LEFT,
        User.tg_user_id != -1,
        ~exists(select(1).select_from(Meetup).where(and_(Meetup.owner_id == User.id, Meetup.active == true()))),
        ~exists(
            select(1)
            .select_from(JoinedUsers)
            .join(Meetup)
            .where(and_(JoinedUsers.user_id == User.id, Meetup.active == true()))
        ),
    )
)
"""Selects IDs of LEFT users who are safe to purge outright.

A LEFT user is retained — not deleted — while they still own an active meeting or hold a join link
to one: deleting them cascades their owned meetings and drops their participation, silently kicking
them out of every meeting they merely stopped receiving messages for. They become purgeable only
once no active meeting depends on them (their dateless meetings deactivate on the LEFT-owner schedule
in `inactive_meetings`, which is how the system converges). A user held here by nothing but a join
link is bounded by the grace period instead: `demote_stale_left_users` moves them to JOINED_ONLY.

JOINED_ONLY users are intentionally excluded — they are cleaned up exclusively
by `inactive_meetings` once none of their meetings remain active.
Invited users (tg_user_id == -1) are handled by `inactive_meetings` too.
"""

# How long a user stays LEFT with everything intact. Past it, a user whose only remaining tie to an
# active meeting is a join link is demoted rather than kept whole indefinitely.
LEFT_STATUS_GRACE_INTERVAL = "30 days"

DEMOTABLE_LEFT_USERS_SELECT_STATEMENT: SelectOfScalar[User] = select(User).where(
    and_(
        User.status == UserStatus.LEFT,
        User.left_time + func.cast(literal(LEFT_STATUS_GRACE_INTERVAL), INTERVAL) < func.now(),
        ~exists(select(1).select_from(Meetup).where(and_(Meetup.owner_id == User.id, Meetup.active == true()))),
        exists(
            select(1)
            .select_from(JoinedUsers)
            .join(Meetup)
            .where(and_(JoinedUsers.user_id == User.id, Meetup.active == true()))
        ),
    )
)
"""Selects LEFT users past the grace period whose only tie to an active meeting is a join link.

Owning an active meeting keeps a user whole: those meetings deactivate on their own schedule in
`inactive_meetings` first, and only then is the user considered here. Invitee placeholders are
created JOINED_ONLY and never transition, so the status filter alone excludes them.

Full rows, not just IDs: the demotion writes each user's status through `User.set_status`.
"""

DELETION_REQUESTED_USERS_SELECT_STATEMENT: SelectOfScalar[User] = select(User).where(
    User.status == UserStatus.DELETION_REQUESTED
)
"""Selects users who asked for their data to be erased.

Full rows, not just IDs: their farewell message needs the chat id and language,
which must be captured before the rows are gone.
"""


@db.with_session
async def demote_stale_left_users(session: AsyncSession) -> int:
    """Demote the LEFT users the grace period has run out on to JOINED_ONLY; returns how many.

    To everyone in their meetings a LEFT user is indistinguishable from a JOINED_ONLY one — someone
    who joined through a button and cannot be reached by DM — and a JOINED_ONLY participant keeps
    their slot while the meeting is active. Demoting therefore drops the account (its owned meetings,
    all inactive by now, and the reachable-member status) without costing anyone the slot they hold,
    and hands the row to the JOINED_ONLY cleanup in `inactive_meetings` once those meetings end.

    The settings row rides along with the user row: a JOINED_ONLY user who later `/start`s becomes a
    MEMBER again, with their language and timezone intact.
    """
    users = (await session.exec(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT)).all()
    if not users:
        return 0

    user_ids = [user.db_id for user in users]
    # Deleting the meetings cascades their join links and message rows. Scoped to inactive ones so
    # the pass can never take down a meeting people are still using.
    await session.exec(delete(Meetup).where(and_(col(Meetup.owner_id).in_(user_ids), Meetup.active == false())))
    for user in users:
        user.set_status(UserStatus.JOINED_ONLY)

    log.info("LEFT users demoted to JOINED_ONLY", count=len(users), user_ids=user_ids)
    return len(users)


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Demote LEFT users past the grace period, then purge LEFT users silently and
    DELETION_REQUESTED users with a farewell message.

    The write lifecycle snapshots each farewell (chat id, text rendered in the user's
    language) at enqueue time, inside the transaction, and sends only after the deletion
    committed: a deletion is never announced before it happened, and a failed send —
    the user blocked the bot, a Telegram hiccup — leaves the deletion standing.

    The demotion runs in its own transaction ahead of the purge: it sends nothing, and the users it
    touches hold a link to an active meeting, which keeps them out of the purge select either way.
    """
    demoted = await demote_stale_left_users()

    async with db.begin_write(api) as session:
        inactive_user_ids = set((await session.exec(INACTIVE_USERS_SELECT_STATEMENT)).all())
        marked_users = (await session.exec(DELETION_REQUESTED_USERS_SELECT_STATEMENT)).all()

        for user in marked_users:
            farewell = MitupView(description=PrivacyMessages.DELETION_COMPLETE.get(lang=user.lang), keyboard=[])
            await api.send_message_to_user(user, farewell)

        user_ids = inactive_user_ids | {user.db_id for user in marked_users}
        # Pending Patreon links carry no foreign key to users, so deleting the user cascades nothing
        # into them. A purge has to name them itself or a deletion request would leave behind a row
        # pairing the requester's Telegram id with their Patreon account.
        purged_pending_links = await pending_links.delete_pending_links_for_users(
            session, [user.tg_user_id for user in marked_users]
        )
        await session.exec(delete(User).where(col(User.id).in_(user_ids)))
        # Rows nobody ever claimed have no user to be purged alongside, so this sweep is the only
        # thing that ever erases them.
        swept_pending_links = await pending_links.delete_finished_pending_links(session)

    metrics.emit(MetricKey.LEFT_USERS_DEMOTED, demoted, MetricUnit.COUNT)
    metrics.emit(MetricKey.INACTIVE_USERS_DELETED, len(inactive_user_ids), MetricUnit.COUNT)
    metrics.emit(MetricKey.PATREON_PENDING_LINKS_DELETED, purged_pending_links + swept_pending_links, MetricUnit.COUNT)
    # Privacy purges are far too sparse for a useful CloudWatch series; the searchable log
    # carries the same information.
    log.info("Deletion-requested users purged", count=len(marked_users), pending_links=purged_pending_links)
    log.info("Finished Patreon pending links swept", count=swept_pending_links)
