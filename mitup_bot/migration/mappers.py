import datetime as dt
from typing import Any, cast

import yaml

from mitup_bot.models import MeetupLocation, MessageButtons
from mitup_bot.models.users import UserStatus
from mitup_bot.views.mitup_view import ButtonConfig


class RowMappingError(ValueError): ...


# Rails (and the new bot) use this sentinel for invited contacts that never registered.
# Each invitation creates its own User row with tg_user_id=-1, so we migrate them just
# like regular users — the JoinedUsers + invitations phases need the row in the audit
# table to graft `invited_by_id` later.
PHANTOM_TG_USER_ID = -1


def map_user_and_settings(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a Rails users row into (user_kwargs, settings_kwargs)."""
    tg_user_id = row.get("tg_user_id")
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")

    user_kwargs: dict[str, Any] = {
        "tg_user_id": tg_user_id,
        "first_name": row["first_name"],
        "last_name": row.get("last_name"),
        "username": row.get("username"),
        "status": map_status(tg_user_id, row.get("active")),
        "created_time": created_at,
        "updated_time": updated_at,
    }

    settings_kwargs: dict[str, Any] = {
        "language": row.get("lang") or "en",
        "timezone": row.get("timezone") or "UTC",
        "notification": coerce_bool(row.get("notif"), default=True),
        "notification_time": coerce_int(row.get("notif_time"), default=5),
        "timeout": coerce_int(row.get("timeout"), default=5),
        "default_waiting_list": coerce_bool(row.get("default_waiting"), default=False),
        "default_public": coerce_bool(row.get("default_public"), default=False),
        "default_allow_invitation": coerce_bool(row.get("default_can_invite"), default=False),
        # Rails `open=True` means "show participants"; new schema's `incognito=True` means "hide them".
        "default_incognito": not coerce_bool(row.get("default_open"), default=False),
        "default_lock_on_start": False,
        # Settings rows are derived from the users row, so reuse the same timestamps.
        "created_time": created_at,
        "updated_time": updated_at,
    }
    return user_kwargs, settings_kwargs


def map_meetup(row: dict[str, Any], owner_new_id: int) -> dict[str, Any]:
    """Convert a Rails meetups row into Meetup kwargs."""
    max_value = row.get("max")
    max_members: int | None = None if max_value is None or int(max_value) < 0 else int(max_value)

    return {
        "owner_id": owner_new_id,
        "title": row.get("title") or "",
        "description": row.get("description"),
        "datetime": row.get("time"),
        "end_datetime": None,
        "max_members": max_members,
        "language": row.get("lang"),
        "location": parse_location(row.get("location")),
        "active": coerce_bool(row.get("active"), default=True),
        "waiting_list": coerce_bool(row.get("waiting"), default=False),
        "public": coerce_bool(row.get("public"), default=False),
        "allow_invitation": coerce_bool(row.get("can_invite"), default=False),
        # Rails `open=True` means "show participants" → new `incognito=False`.
        "incognito": not coerce_bool(row.get("open"), default=False),
        "started_notification_sent": False,
        "expiration_notification_sent": False,
        "lock_on_start": False,
        "expiration_time": None,
        "created_time": row.get("created_at"),
        "updated_time": row.get("updated_at"),
    }


def map_join(
    row: dict[str, Any],
    *,
    user_new_id: int,
    meetup_new_id: int,
    is_waiting_list: bool,
    notification_pending: bool,
) -> dict[str, Any]:
    """Convert a Rails user_join_meetups or user_waiting_lists row into JoinedUsers kwargs.

    `notification_pending=True` flips `notification_sent` to False — the new schema's flag is the
    inverse of the legacy "still pending in the notifications table" signal.
    """
    return {
        "user_id": user_new_id,
        "meetup_id": meetup_new_id,
        "is_waiting_list": is_waiting_list,
        "notification_sent": not notification_pending,
        "created_time": row.get("created_at") or dt.datetime.now(dt.UTC),
    }


def map_invitation(host_new_id: int) -> dict[str, Any]:
    """Return the partial JoinedUsers update for an invitation — just `invited_by_id`."""
    return {"invited_by_id": host_new_id}


def map_message(row: dict[str, Any], meetup_new_id: int) -> dict[str, Any]:
    """Convert a Rails messages row into Message kwargs."""
    return {
        "message_id": row.get("msg_id"),
        "chat_id": row.get("chat_id"),
        "inline_message_id": row.get("inline_msg_id"),
        "chat_instance": None,
        "meetup_id": meetup_new_id,
        "buttons": parse_buttons(row.get("buttons")),
    }


def parse_location(blob: object) -> MeetupLocation:
    if blob is None or blob == "":
        return MeetupLocation()

    parsed: Any
    try:
        parsed = yaml.safe_load(blob) if isinstance(blob, str) else blob
    except yaml.YAMLError as exc:
        raise RowMappingError(f"unparseable location blob: {exc}") from exc

    if not isinstance(parsed, dict):
        return MeetupLocation()
    parsed_map = cast(dict[str, Any], parsed)
    name_raw = parsed_map.get("name") or parsed_map.get(":name")
    coord_raw = parsed_map.get("coord") or parsed_map.get(":coord")
    name = str(name_raw) if name_raw not in (None, "") else None

    coordinates: tuple[float, float] | None = None
    if isinstance(coord_raw, (list, tuple)) and len(coord_raw) == 2:
        lat, lon = (to_float(v) for v in coord_raw)
        if lat is not None and lon is not None:
            coordinates = (lat, lon)

    return MeetupLocation(name=name, coordinates=coordinates)


def parse_buttons(blob: object) -> MessageButtons:
    if blob is None or blob == "":
        return MessageButtons(keyboard=[])

    try:
        parsed = yaml.safe_load(blob) if isinstance(blob, str) else blob
    except yaml.YAMLError:
        return MessageButtons(keyboard=[])

    if not isinstance(parsed, dict):
        return MessageButtons(keyboard=[])
    parsed_map = cast(dict[str, Any], parsed)
    raw_keyboard = parsed_map.get("keyboard") or parsed_map.get(":keyboard") or []
    if not isinstance(raw_keyboard, list):
        return MessageButtons(keyboard=[])

    keyboard: list[list[ButtonConfig]] = []
    for raw_row in raw_keyboard:
        if not isinstance(raw_row, list):
            continue
        row: list[ButtonConfig] = []
        for raw_button in raw_row:
            button = parse_button(raw_button)
            if button is not None:
                row.append(button)
        if row:
            keyboard.append(row)
    return MessageButtons(keyboard=keyboard)


def parse_button(raw: object) -> ButtonConfig | None:
    if not isinstance(raw, dict):
        return None
    raw_map = cast(dict[str, Any], raw)
    text = raw_map.get("text") or raw_map.get(":text")
    callback_data = raw_map.get("callback_data") or raw_map.get(":callback_data")
    if not text:
        return None
    if callback_data is not None and not isinstance(callback_data, str):
        return None
    return ButtonConfig(text=str(text), callback_data=callback_data)


def to_float(value: object) -> float | None:
    """Best-effort float conversion. Returns None for unparseable / None inputs."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def map_status(tg_user_id: object, active: object) -> UserStatus:
    """Translate Rails `(tg_user_id, active)` into the new `UserStatus` enum.

    Sentinel rows (tg_user_id == -1) are invitee placeholders that never engaged with
    the bot, so JOINED_ONLY captures them more accurately than the legacy
    `active=False` they carry in Rails. Real users follow active → MEMBER, !active →
    LEFT (matches the issue #163 backfill rule).
    """
    if tg_user_id == PHANTOM_TG_USER_ID:
        return UserStatus.JOINED_ONLY
    return UserStatus.MEMBER if coerce_bool(active, default=False) else UserStatus.LEFT


def coerce_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"t", "true", "1", "yes"}
    return default


def coerce_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
