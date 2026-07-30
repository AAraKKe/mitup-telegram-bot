"""Self-service personal-data export (GDPR Art. 20).

Builds a structured, machine-readable snapshot of everything the bot stores about one
user. Other people appear as display names only — never their Telegram ids or internal
database ids — matching exactly what the exporting user already sees in the bot.
"""

import datetime as dt
import json
from typing import Any, cast

from sqlalchemy.orm import QueryableAttribute, selectinload
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot.models import JoinedUsers, Meetup, PatreonPendingLink, Settings, SupporterSubscription, User

EXPORT_BOT_NAME = "Mitup"


def iso_utc(value: dt.datetime | None) -> str | None:
    """ISO 8601 in UTC; naive values are stored as UTC in the database, so the offset is made explicit."""
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    return aware.astimezone(dt.UTC).isoformat()


def owned_meetings_statement(user: User) -> SelectOfScalar[Meetup]:
    """The user's meetings with participant links and their users eagerly loaded.

    The chain must be spelled out: relationship-level selectin loading stops where the
    path cycles back to `User` (meetups -> joined_links -> user), so participants reached
    through `User.meetups` are not loaded and would raise under the async engine.
    """
    return (
        select(Meetup)
        .where(Meetup.owner_id == user.db_id)
        .order_by(col(Meetup.id))
        .options(
            selectinload(cast("QueryableAttribute[Any]", Meetup.joined_links)).selectinload(
                cast("QueryableAttribute[Any]", JoinedUsers.user)
            )
        )
    )


def joined_links_statement(user: User) -> SelectOfScalar[JoinedUsers]:
    """The user's joins with each meeting and its organizer eagerly loaded (same cycle
    limitation as `owned_meetings_statement`, here on joined_links -> meetup -> owner)."""
    return (
        select(JoinedUsers)
        .where(JoinedUsers.user_id == user.db_id)
        .order_by(col(JoinedUsers.id))
        .options(
            selectinload(cast("QueryableAttribute[Any]", JoinedUsers.meetup)).selectinload(
                cast("QueryableAttribute[Any]", Meetup.owner)
            )
        )
    )


def subscription_statement(user: User) -> SelectOfScalar[SupporterSubscription]:
    return select(SupporterSubscription).where(SupporterSubscription.user_id == user.db_id)


def pending_links_statement(user: User) -> SelectOfScalar[PatreonPendingLink]:
    """Pending Patreon links this user claimed.

    Only claimed rows are addressable: an unclaimed one carries no Telegram identifier at all, so
    there is nothing that marks it as this user's to export.
    """
    return select(PatreonPendingLink).where(PatreonPendingLink.claimed_tg_user_id == user.tg_user_id)


def user_section(user: User) -> dict[str, Any]:
    return {
        "telegram_user_id": user.tg_user_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "status": user.status.value,
        "supporter_level": user.supporter_level.value,
        "acquisition_source": user.acquisition_source,
        "created_time": iso_utc(user.created_time),
        "member_time": iso_utc(user.member_time),
    }


def settings_section(settings: Settings) -> dict[str, Any]:
    return {
        "language": settings.language,
        "timezone": settings.timezone,
        "notification": settings.notification,
        "notification_time": settings.notification_time,
        "timeout": settings.timeout,
        "default_waiting_list": settings.default_waiting_list,
        "default_public": settings.default_public,
        "default_allow_invitation": settings.default_allow_invitation,
        "default_incognito": settings.default_incognito,
        "default_lock_on_start": settings.default_lock_on_start,
    }


def owned_meeting_section(meeting: Meetup) -> dict[str, Any]:
    return {
        "title": meeting.plain_title,
        "description": meeting.plain_description,
        "start_datetime": iso_utc(meeting.datetime),
        "end_datetime": iso_utc(meeting.end_datetime),
        "created_time": iso_utc(meeting.created_time),
        "language": meeting.language,
        "location": {"name": meeting.location.coerced_name, "coordinates": meeting.location.coordinates},
        "active": meeting.active,
        "max_participants": meeting.max_members,
        "waiting_list_enabled": meeting.waiting_list,
        "public": meeting.public,
        "allow_invitations": meeting.allow_invitation,
        "incognito": meeting.incognito,
        "lock_on_start": meeting.lock_on_start,
        "participants": [link.user.display_name for link in meeting.joined_links if not link.is_waiting_list],
        "waiting_list": [link.user.display_name for link in meeting.joined_links if link.is_waiting_list],
    }


def joined_meeting_section(link: JoinedUsers) -> dict[str, Any]:
    return {
        "meeting_title": link.meetup.plain_title,
        "meeting_start_datetime": iso_utc(link.meetup.datetime),
        "organizer": link.meetup.owner.display_name,
        "joined_time": iso_utc(link.created_time),
        "on_waiting_list": link.is_waiting_list,
    }


def patreon_section(subscription: SupporterSubscription) -> dict[str, Any]:
    return {
        "patreon_user_id": subscription.patreon_user_id,
        "support_expiration": iso_utc(subscription.support_expiration),
        "linked_time": iso_utc(subscription.created_time),
    }


def pending_patreon_link_section(pending: PatreonPendingLink) -> dict[str, Any]:
    """A Patreon link this user opened but has not completed. Short lived, but real data about
    them while it exists, so an export has to show it. The pairing code is not included because it
    is not stored — only a hash of it is."""
    return {
        "patreon_user_id": pending.patreon_user_id,
        "patreon_display_name": pending.patreon_full_name,
        "claimed_time": iso_utc(pending.claimed_time),
        "expiration": iso_utc(pending.expiration),
        "confirmed": pending.consumed_time is not None,
    }


async def build_user_export(session: AsyncSession, user: User) -> dict[str, Any]:
    """Assemble the full export envelope for `user`.

    Loads every traversal itself, so `user` needs none of its collections loaded.
    Joins to the user's own meetings are omitted from `joined_meetings`:
    those meetings already appear in full under `meetings`.
    """
    meetings = (await session.exec(owned_meetings_statement(user))).all()
    joined_links = (await session.exec(joined_links_statement(user))).all()
    subscription = (await session.exec(subscription_statement(user))).first()
    pending_links = (await session.exec(pending_links_statement(user))).all()
    return {
        "bot": EXPORT_BOT_NAME,
        "exported_at": iso_utc(dt.datetime.now(dt.UTC)),
        "user": user_section(user),
        "settings": settings_section(user.settings),
        "meetings": [owned_meeting_section(meeting) for meeting in meetings],
        "joined_meetings": [joined_meeting_section(link) for link in joined_links if not link.meetup.is_owned_by(user)],
        "patreon": patreon_section(subscription) if subscription else None,
        "pending_patreon_links": [pending_patreon_link_section(pending) for pending in pending_links],
    }


def export_scope(export: dict[str, Any]) -> dict[str, Any]:
    """The counts that evidence what one disclosure contained.

    The exported document itself is never retained, so the line that records the disclosure is the
    only lasting evidence of its scope.
    """
    return {
        "owned_meetings": len(export["meetings"]),
        "joined_meetings": len(export["joined_meetings"]),
        "has_patreon": export["patreon"] is not None,
        "pending_patreon_links": len(export["pending_patreon_links"]),
    }


def export_document(export: dict[str, Any]) -> tuple[bytes, str]:
    """Serialize an export envelope to JSON bytes plus its download filename (UTC date)."""
    filename = f"mitup-export-{dt.datetime.now(dt.UTC):%Y-%m-%d}.json"
    return json.dumps(export, ensure_ascii=False, indent=2).encode(), filename
