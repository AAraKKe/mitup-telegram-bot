from collections import Counter
from collections.abc import Collection
from enum import StrEnum
from typing import cast

import structlog
from sqlalchemy import ColumnElement
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlmodel import and_, col, delete, exists, false, func, literal, or_, select, true
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.deletion import mark_meeting_cards_deleted
from mitup_bot.models import JoinedUsers, Meetup, Message, User
from mitup_bot.models.meetups import log_waiting_list_promotions
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from mitup_bot.patreon import pending_links
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views import MitupView

from .telemetry import error_type_name

log = structlog.get_logger(__name__)

# Repeated in the purge DELETE, so a LEFT user who came back or joined an active meeting after the
# select keeps the row.
PURGEABLE_LEFT_USER: ColumnElement[bool] = and_(
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

INACTIVE_USERS_SELECT_STATEMENT = select(User.id, User.tg_user_id).where(PURGEABLE_LEFT_USER)
"""Selects the ids of LEFT users who are safe to purge outright, with the Telegram id each maps to.

Both ids, not just the primary key: the rows are hard-deleted and this select is the only moment a
silently erased account's identity can still be recorded.

A LEFT user is retained — not deleted — while they still own an active meeting or hold a join link
to one: deleting them cascades their owned meetings and drops their participation, silently kicking
them out of every meeting they merely stopped receiving messages for. They become purgeable only
once no active meeting depends on them (their dateless meetings deactivate on the LEFT-owner schedule
in `inactive_meetings`, which is how the system converges). A user held here by nothing but a join
link is bounded by the grace period instead: `demote_stale_left_users` moves them to JOINED_ONLY.

JOINED_ONLY users are intentionally excluded — they are cleaned up exclusively
by `inactive_meetings` once they own no meeting and none they joined remains active. ONBOARDING
users are cleaned up there too.
Invited users (tg_user_id == -1) are handled by `inactive_meetings` too.
"""

# How long a user stays LEFT with everything intact. Past it, a user whose only remaining tie to an
# active meeting is a join link is demoted rather than kept whole indefinitely. Kept as a number so
# the rule the demotion ran under is a queryable field and not only a fragment of SQL.
LEFT_STATUS_GRACE_DAYS = 30
LEFT_STATUS_GRACE_INTERVAL = f"{LEFT_STATUS_GRACE_DAYS} days"

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

# Only a meeting with a tracked card has anything to rewrite before it is deleted.
HAS_TRACKED_CARD: ColumnElement[bool] = exists(select(1).select_from(Message).where(Message.meetup_id == Meetup.id))

DELETION_REQUESTED_USER_IDS_STATEMENT = select(User.id).where(User.status == UserStatus.DELETION_REQUESTED)
"""Selects the users a run erases: one snapshot read before the leave step, so a request confirmed
while the run is underway waits for the next run."""

DELETION_REQUESTED_USERS_SELECT_STATEMENT: SelectOfScalar[User] = select(User).where(
    User.status == UserStatus.DELETION_REQUESTED
)
"""Selects users who asked for their data to be erased.

Full rows, not just IDs: their farewell message needs the chat id and language,
which must be captured before the rows are gone.
"""

MEETINGS_JOINED_BY_DELETION_REQUESTED_USERS_STATEMENT = (
    select(Meetup.id)
    .distinct()
    .join(JoinedUsers, col(JoinedUsers.meetup_id) == col(Meetup.id))
    .join(User, col(JoinedUsers.user_id) == col(User.id))
    .where(
        User.status == UserStatus.DELETION_REQUESTED,
        Meetup.active == true(),
        col(Meetup.owner_id) != col(User.id),
    )
)
"""Selects the active meetings a deletion-requested user joined without owning them.

Ids only: each meeting is re-read under its row lock before anybody is taken out of it.
"""


async def demote_users_still_demotable(
    session: AsyncSession, api: TelegramApiWrapper, candidate_ids: Collection[int]
) -> list[User]:
    """Demote the candidates that still qualify once their rows are locked, deleting their inactive
    meetings and rewriting those meetings' cards; returns the users it demoted.

    The lock holds each row until the commit, so nobody stops being LEFT between this check and the write.
    """
    statement = (
        DEMOTABLE_LEFT_USERS_SELECT_STATEMENT.where(col(User.id).in_(candidate_ids))
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    users = (await session.exec(statement)).all()
    if not users:
        return []

    user_ids = [user.db_id for user in users]
    # Scoped to inactive meetings so the pass can never take down a meeting people are still using.
    owned_inactive = and_(col(Meetup.owner_id).in_(user_ids), Meetup.active == false())
    carded = (await session.exec(select(Meetup).where(owned_inactive, HAS_TRACKED_CARD))).all()
    await mark_meeting_cards_deleted(api, carded)
    # Deleting the meetings cascades their join links and message rows.
    result = await session.exec(delete(Meetup).where(owned_inactive))

    for user in users:
        # Ahead of the flip, which clears the stamp that says which grace period ran out.
        log.info(
            "LEFT user demoted",
            user_id=user.db_id,
            tg_user_id=user.tg_user_id,
            left_time=user.left_time,
            grace_days=LEFT_STATUS_GRACE_DAYS,
            reason="left_grace_expired_join_link_only",
        )
        user.set_status(UserStatus.JOINED_ONLY)

    log.info(
        "LEFT users demoted to JOINED_ONLY",
        count=len(users),
        user_ids=user_ids,
        meetups_deleted=result.rowcount or 0,
        grace_days=LEFT_STATUS_GRACE_DAYS,
    )
    return list(users)


async def demote_stale_left_users(api: TelegramApiWrapper) -> int:
    """Demote the LEFT users the grace period has run out on to JOINED_ONLY; returns how many.

    To everyone in their meetings a LEFT user is indistinguishable from a JOINED_ONLY one (someone
    who joined through a button and cannot be reached by DM), and a JOINED_ONLY participant keeps
    their slot while the meeting is active. Demoting therefore drops the account (its owned meetings,
    all inactive by now, and the reachable-member status) without costing anyone the slot they hold,
    and hands the row to the JOINED_ONLY cleanup in `inactive_meetings` once those meetings end.

    The settings row rides along with the user row: a JOINED_ONLY user who later `/start`s becomes a
    MEMBER again, with their language and timezone intact.
    """
    async with db.begin_write(api) as session:
        candidate_ids = {user.db_id for user in (await session.exec(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT)).all()}
        if not candidate_ids:
            return 0
        demoted = await demote_users_still_demotable(session, api, candidate_ids)
        for user_id in sorted(candidate_ids - {user.db_id for user in demoted}):
            log.info("LEFT user demotion skipped", user_id=user_id, reason="no_longer_demotable")
        return len(demoted)


class LeaveSkipReason(StrEnum):
    """Why a nominated meeting kept every link once its row lock was taken."""

    MEETING_DELETED = "meeting_deleted"
    ALREADY_INACTIVE = "already_inactive"
    OWNER_DELETION_REQUESTED = "owner_deletion_requested"
    NO_DELETION_REQUESTED_PARTICIPANT = "no_deletion_requested_participant"


def leave_skip_reason(
    meeting: Meetup | None, leaving: list[JoinedUsers], leaving_user_ids: Collection[int]
) -> LeaveSkipReason | None:
    """Why the locked meeting keeps every link, or None when somebody leaves it."""
    if meeting is None:
        return LeaveSkipReason.MEETING_DELETED
    if not meeting.active:
        return LeaveSkipReason.ALREADY_INACTIVE
    if meeting.owner.id in leaving_user_ids:
        # The purge deletes this meeting with its owner and rewrites its cards as deleted.
        return LeaveSkipReason.OWNER_DELETION_REQUESTED
    if not leaving:
        return LeaveSkipReason.NO_DELETION_REQUESTED_PARTICIPANT
    return None


async def remove_deletion_requested_participants(
    session: AsyncSession, api: TelegramApiWrapper, meeting_id: int, leaving_user_ids: Collection[int]
) -> int:
    """Take the deletion-requested participants among *leaving_user_ids* out of one active meeting
    under its row lock, the way leaving does, and refresh its cards; returns how many were taken out.

    All of them leave before the waiting list moves, so nobody who is leaving is promoted.
    """
    meeting = await Meetup.by_id(session, meeting_id, include_inactive=True, for_update=True)
    leaving = [
        link
        for link in (meeting.joined_links if meeting is not None else [])
        if link.user_id in leaving_user_ids and link.user.status is UserStatus.DELETION_REQUESTED
    ]
    if (skip_reason := leave_skip_reason(meeting, leaving, leaving_user_ids)) is not None:
        log.info("Deleted account meeting leave skipped", reason=skip_reason.value)
        return 0
    assert meeting is not None, "leave_skip_reason answers for a missing meeting"

    for link in leaving:
        meeting.joined_links.remove(link)
    promoted_links = meeting.promote_from_waiting_list()

    for link in leaving:
        log.info(
            "Meeting leave resolved",
            leaving_user_id=link.user_id,
            outcome="left",
            was_waiting_list=link.is_waiting_list,
            n_participants=meeting.n_participants,
            effective_max_members=meeting.effective_max_members,
            reason="account_deletion_requested",
        )
    log_waiting_list_promotions(meeting, promoted_links, reason="participant_account_deleted")
    await api.notify_users_promoted_from_waiting_list(joined_users=promoted_links, meeting=meeting)
    await api.update_meeting_messages(meeting=meeting)
    return len(leaving)


async def leave_joined_meetings(api: TelegramApiWrapper, deletion_requested_ids: Collection[int]) -> tuple[int, int]:
    """Take *deletion_requested_ids* out of the active meetings they joined, one meeting per
    transaction; returns how many links went and how many meetings failed.

    A failed meeting keeps its link for the purge's cascade, so the erasure is never held back.
    """
    if not deletion_requested_ids:
        return 0, 0
    async with db.begin() as session:
        statement = MEETINGS_JOINED_BY_DELETION_REQUESTED_USERS_STATEMENT.where(
            col(User.id).in_(deletion_requested_ids)
        )
        meeting_ids = (await session.exec(statement)).all()

    removed = failed = 0
    for meeting_id in meeting_ids:
        with structlog.contextvars.bound_contextvars(meeting_id=meeting_id):
            try:
                async with db.begin_write(api) as session:
                    taken_out = await remove_deletion_requested_participants(
                        session, api, cast(int, meeting_id), deletion_requested_ids
                    )
                removed += taken_out
            except Exception as error:
                failed += 1
                log.exception(
                    "Failed to remove a deleted account from a meeting",
                    error_type=error_type_name(error),
                    exc_info=error,
                    reason="leave_failed",
                )
    return removed, failed


async def purge_cascade_counts(
    session: AsyncSession, user_ids: Collection[int]
) -> tuple[Counter[int | None], Counter[int | None]]:
    """How many meetings each user owns and how many join links each holds, counted while the rows
    exist: `cascade_delete=True` destroys both without naming them."""
    owner_id = col(Meetup.owner_id)
    member_id = col(JoinedUsers.user_id)
    owned = await session.exec(select(owner_id, func.count()).where(owner_id.in_(user_ids)).group_by(owner_id))
    links = await session.exec(select(member_id, func.count()).where(member_id.in_(user_ids)).group_by(member_id))
    return Counter(dict(owned.all())), Counter(dict(links.all()))


async def delete_users_still_purgeable(
    session: AsyncSession, left_user_ids: Collection[int], deletion_requested_ids: Collection[int]
) -> set[int]:
    """Delete the selected users that still qualify when the DELETE runs, and return the ids it took.

    A LEFT user who came back or joined an active meeting since the select keeps the row, and so
    does a deletion request that is no longer standing.
    """
    statement = (
        delete(User)
        .where(
            or_(
                and_(col(User.id).in_(left_user_ids), PURGEABLE_LEFT_USER),
                and_(col(User.id).in_(deletion_requested_ids), User.status == UserStatus.DELETION_REQUESTED),
            )
        )
        .returning(col(User.id))
    )
    return {row[0] for row in (await session.exec(statement)).all()}


async def run(api: TelegramApiWrapper, metrics: MetricsClient):
    """Demote LEFT users past the grace period, then purge LEFT users silently and
    DELETION_REQUESTED users with a farewell message.

    The write lifecycle snapshots each farewell (chat id, text rendered in the user's
    language) at enqueue time, inside the transaction, and sends only after the deletion
    committed: a deletion is never announced before it happened, and a failed send (the user
    blocked the bot, a Telegram hiccup) leaves the deletion standing.

    Every meeting either pass deletes has its tracked cards rewritten as deleted, after the commit.

    The demotion runs in its own transaction ahead of the purge: the users it touches hold a link to
    an active meeting, which keeps them out of the purge select either way. Deletion-requested users
    then leave the active meetings they joined, one meeting per transaction, before their rows go.
    """
    demoted = await demote_stale_left_users(api)

    async with db.begin() as session:
        deletion_requested_ids = set((await session.exec(DELETION_REQUESTED_USER_IDS_STATEMENT)).all())
    joined_links_removed, failed_meetings = await leave_joined_meetings(api, deletion_requested_ids)

    async with db.begin_write(api) as session:
        inactive_users = (await session.exec(INACTIVE_USERS_SELECT_STATEMENT)).all()
        inactive_user_ids = {user_id for user_id, _ in inactive_users}
        marked_users = (
            await session.exec(
                DELETION_REQUESTED_USERS_SELECT_STATEMENT.where(col(User.id).in_(deletion_requested_ids))
            )
        ).all()
        marked_user_ids = {user.db_id for user in marked_users}
        selected_ids = inactive_user_ids | marked_user_ids

        owned_meetups, join_links = await purge_cascade_counts(session, selected_ids)
        # Loaded while the rows exist. The deleted banner renders from what is already in memory, so
        # only the meetings of users the DELETE actually took are rewritten, after it.
        cascaded = select(Meetup).where(col(Meetup.owner_id).in_(selected_ids), HAS_TRACKED_CARD)
        carded_meetups = (await session.exec(cascaded)).all()

        purged_ids = await delete_users_still_purgeable(session, inactive_user_ids, marked_user_ids)
        purged_marked_users = [user for user in marked_users if user.db_id in purged_ids]

        for user_id, tg_user_id in inactive_users:
            if user_id in purged_ids:
                log.info(
                    "User purged", user_id=user_id, tg_user_id=tg_user_id, reason="left_and_no_active_meeting_or_join"
                )
        for user in purged_marked_users:
            farewell = MitupView(message=PrivacyMessages.DELETION_COMPLETE.rich(lang=user.lang), menu=[])
            await api.send_message_to_user(user, farewell)
            log.info("Deletion farewell queued", user_id=user.db_id, tg_user_id=user.tg_user_id, lang=user.lang)
            log.info("User purged", user_id=user.db_id, tg_user_id=user.tg_user_id, reason="deletion_requested")
        for user_id in sorted(selected_ids - purged_ids):
            log.info("User purge skipped", user_id=user_id, reason="no_longer_purgeable")

        if purged_ids:
            log.info(
                "User purge cascade",
                user_ids=sorted(purged_ids),
                meetups_deleted=sum(owned_meetups[user_id] for user_id in purged_ids),
                join_links_deleted=sum(join_links[user_id] for user_id in purged_ids),
                reason="user_row_deleted",
            )
        await mark_meeting_cards_deleted(api, [meetup for meetup in carded_meetups if meetup.owner.id in purged_ids])
        # Pending Patreon links carry no foreign key to users, so deleting the user cascades nothing
        # into them. A purge has to name them itself or a deletion request would leave behind a row
        # pairing the requester's Telegram id with their Patreon account.
        purged_pending_links = await pending_links.delete_pending_links_for_users(
            session, [user.tg_user_id for user in purged_marked_users]
        )
        # Rows nobody ever claimed have no user to be purged alongside, so this sweep is the only
        # thing that ever erases them.
        swept_pending_links = await pending_links.delete_finished_pending_links(session)

    # Privacy purges are far too sparse for a useful CloudWatch series; the searchable log
    # carries the same information.
    log.info("Deletion-requested users purged", count=len(purged_marked_users), pending_links=purged_pending_links)
    log.info("Finished Patreon pending links swept", count=swept_pending_links)
    log.info(
        "User cleanup complete",
        demoted=demoted,
        inactive_purged=len(inactive_user_ids & purged_ids),
        deletion_requested_purged=len(purged_marked_users),
        joined_links_removed=joined_links_removed,
        failed_meetings=failed_meetings,
        pending_links_purged=purged_pending_links + swept_pending_links,
        grace_days=LEFT_STATUS_GRACE_DAYS,
    )

    if failed_meetings:
        raise RuntimeError(
            f"Failed to remove deleted accounts from {failed_meetings} meetings. Check logs for details."
        )
