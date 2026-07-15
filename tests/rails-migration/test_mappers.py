import datetime as dt

import pytest

from mitup_bot.migration.mappers import (
    PHANTOM_TG_USER_ID,
    RowMappingError,
    coerce_bool,
    coerce_int,
    map_invitation,
    map_join,
    map_meetup,
    map_message,
    map_user_and_settings,
    parse_buttons,
    parse_location,
)
from mitup_bot.models.users import UserStatus


def test_map_user_and_settings_copies_core_fields_and_splits_settings():
    created = dt.datetime(2023, 6, 1, 12, 30, tzinfo=dt.UTC)
    updated = dt.datetime(2024, 1, 15, 9, 0, tzinfo=dt.UTC)
    row = {
        "id": 42,
        "tg_user_id": 12345,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "username": "ada",
        "active": True,
        "lang": "es",
        "timezone": "Europe/Madrid",
        "notif": True,
        "notif_time": 10,
        "timeout": 7,
        "default_waiting": True,
        "default_public": False,
        "default_can_invite": True,
        "default_open": True,
        "default_show_tz": True,
        "created_at": created,
        "updated_at": updated,
    }

    user, settings = map_user_and_settings(row)

    assert user == {
        "tg_user_id": 12345,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "username": "ada",
        "status": UserStatus.MEMBER,
        "created_time": created,
        "updated_time": updated,
    }
    assert settings["language"] == "es_ES"
    assert settings["timezone"] == "Europe/Madrid"
    assert settings["notification"] is True
    assert settings["notification_time"] == 10
    assert settings["timeout"] == 7
    assert settings["default_waiting_list"] is True
    assert settings["default_public"] is False
    assert settings["default_allow_invitation"] is True
    # default_open=True (show participants) inverts to default_incognito=False
    assert settings["default_incognito"] is False
    assert settings["default_lock_on_start"] is False
    # Settings inherits its timestamps from the same Rails users row.
    assert settings["created_time"] == created
    assert settings["updated_time"] == updated


def test_map_user_migrates_phantom_as_joined_only_invited_placeholder():
    # Phantom rows (tg_user_id == -1) are invitee placeholders that never engaged with
    # the bot. JOINED_ONLY captures that more accurately than the legacy `active=False`
    # they carry in Rails. Downstream phases rely on the audit-table mapping to attach
    # their joined_users + invitations rows.
    row = {"id": 1, "tg_user_id": PHANTOM_TG_USER_ID, "first_name": "ghost", "active": False}
    user, settings = map_user_and_settings(row)
    assert user["tg_user_id"] == PHANTOM_TG_USER_ID
    assert user["first_name"] == "ghost"
    assert user["status"] is UserStatus.JOINED_ONLY
    assert settings["language"] == "en"


@pytest.mark.parametrize(
    "rails_lang,locale",
    [("en", "en"), ("es", "es_ES"), ("gl", "gl_ES"), ("de", "de_DE"), ("pt", "pt_BR"), ("it", "it_IT")],
)
def test_map_user_translates_every_rails_language_code(rails_lang: str, locale: str):
    row = {"id": 1, "tg_user_id": 5, "first_name": "Polyglot", "active": True, "lang": rails_lang}
    _, settings = map_user_and_settings(row)
    assert settings["language"] == locale


def test_map_user_unknown_language_falls_back_to_english():
    row = {"id": 1, "tg_user_id": 5, "first_name": "Mystery", "active": True, "lang": "fr"}
    _, settings = map_user_and_settings(row)
    assert settings["language"] == "en"


def test_map_meetup_translates_language_and_keeps_none():
    assert map_meetup({"id": 1, "title": "Hi", "lang": "gl"}, owner_new_id=1)["language"] == "gl_ES"
    assert map_meetup({"id": 1, "title": "Hi"}, owner_new_id=1)["language"] is None


def test_map_user_inactive_real_user_becomes_left():
    row = {"id": 8, "tg_user_id": 100, "first_name": "Gone", "active": False}
    user, _ = map_user_and_settings(row)
    assert user["status"] is UserStatus.LEFT


def test_map_user_defaults_missing_fields():
    row = {"id": 7, "tg_user_id": 99, "first_name": "Bare"}
    user, settings = map_user_and_settings(row)
    # Missing `active` defaults to False → LEFT for real users.
    assert user["status"] is UserStatus.LEFT
    assert settings["language"] == "en"
    assert settings["timezone"] == "UTC"
    assert settings["notification"] is True
    assert settings["notification_time"] == 5
    assert settings["timeout"] == 5
    # default_open is None → False → default_incognito = True
    assert settings["default_incognito"] is True


def test_map_meetup_max_negative_one_becomes_unlimited():
    row = {
        "id": 1,
        "title": "Hi",
        "max": -1,
        "owner_id": 1,
        "time": None,
        "lang": "en",
        "location": None,
        "active": True,
        "waiting": False,
        "public": False,
        "can_invite": False,
        "open": False,
    }
    kw = map_meetup(row, owner_new_id=10)
    assert kw["max_members"] is None
    assert kw["owner_id"] == 10
    assert kw["incognito"] is True  # open=False → incognito=True


def test_map_meetup_max_positive_passes_through():
    row = {"id": 1, "title": "Hi", "max": 5, "open": True}
    kw = map_meetup(row, owner_new_id=10)
    assert kw["max_members"] == 5
    assert kw["incognito"] is False  # open=True → incognito=False


def test_map_meetup_parses_yaml_location():
    yaml_blob = "---\n:name: Library\n:coord:\n- 40.4\n- -3.7\n"
    row = {"id": 1, "title": "T", "max": -1, "location": yaml_blob}
    kw = map_meetup(row, owner_new_id=1)
    assert kw["location"].name == "Library"
    assert kw["location"].coordinates == pytest.approx((40.4, -3.7))


def test_map_meetup_handles_empty_yaml_location():
    yaml_blob = "---\n:name:\n:coord:\n- \n- \n"
    row = {"id": 1, "title": "T", "max": -1, "location": yaml_blob}
    kw = map_meetup(row, owner_new_id=1)
    assert kw["location"].name is None
    assert kw["location"].coordinates is None


def test_map_join_carries_through_created_time_and_inverts_notification_pending():
    created = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    row = {"id": 1, "user_id": 2, "meetup_id": 3, "created_at": created}
    kw = map_join(row, user_new_id=20, meetup_new_id=30, is_waiting_list=True, notification_pending=True)
    assert kw["user_id"] == 20
    assert kw["meetup_id"] == 30
    assert kw["is_waiting_list"] is True
    # Rails still has a pending notification → new schema marks notification_sent=False.
    assert kw["notification_sent"] is False
    assert kw["created_time"] == created


def test_map_invitation_returns_invited_by_update():
    assert map_invitation(host_new_id=7) == {"invited_by_id": 7}


def test_map_message_carries_buttons_yaml():
    yaml_buttons = "---\n:keyboard:\n- - :text: Join\n    :callback_data: join:1\n"
    row = {
        "id": 1,
        "msg_id": 100,
        "chat_id": -1001234,
        "inline_msg_id": None,
        "meetup_id": 1,
        "buttons": yaml_buttons,
    }
    kw = map_message(row, meetup_new_id=5)
    assert kw["message_id"] == 100
    assert kw["chat_id"] == -1001234
    assert kw["meetup_id"] == 5
    assert kw["chat_instance"] is None
    assert len(kw["buttons"].keyboard) == 1
    assert kw["buttons"].keyboard[0][0].text == "Join"
    assert kw["buttons"].keyboard[0][0].callback_data == "join:1"


def test_map_message_unparseable_buttons_returns_empty_keyboard():
    row = {"id": 1, "msg_id": 1, "meetup_id": 1, "buttons": "this is not yaml: {[}"}
    kw = map_message(row, meetup_new_id=1)
    assert kw["buttons"].keyboard == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("t", True),
        ("yes", True),
        ("1", True),
        ("TRUE", True),  # case-insensitive
        ("  true  ", True),  # whitespace-stripped
        ("false", False),  # unrecognized string → default (False here)
        ("garbage", False),
        ("", False),
    ],
)
def test_coerce_bool_string_inputs(raw: str, expected: bool):
    # Default is False so unrecognized strings fall through to default.
    assert coerce_bool(raw, default=False) is expected


def test_coerce_bool_none_returns_default():
    assert coerce_bool(None, default=True) is True
    assert coerce_bool(None, default=False) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0),
        (1, 1),
        (42, 42),
        (-1, -1),
    ],
)
def test_coerce_int_int_inputs(raw: int, expected: int):
    assert coerce_int(raw, default=99) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("42", 42),
        ("-1", -1),
        ("not-a-number", 99),  # falls back to default
        ("", 99),
    ],
)
def test_coerce_int_string_inputs(raw: str, expected: int):
    assert coerce_int(raw, default=99) == expected


def test_map_meetup_max_none_becomes_unlimited():
    row = {"id": 1, "title": "T", "max": None}
    kw = map_meetup(row, owner_new_id=1)
    assert kw["max_members"] is None


def test_map_join_missing_created_at_defaults_to_now_utc():
    row = {"id": 1, "user_id": 1, "meetup_id": 1, "created_at": None}
    kw = map_join(row, user_new_id=10, meetup_new_id=20, is_waiting_list=False, notification_pending=False)
    created = kw["created_time"]
    assert isinstance(created, dt.datetime)
    assert created.tzinfo == dt.UTC


def test_parse_location_non_dict_yaml_returns_empty_location():
    # YAML list — valid YAML but not a mapping.
    yaml_list = "---\n- foo\n- bar\n"
    location = parse_location(yaml_list)
    assert location.name is None
    assert location.coordinates is None


def test_parse_buttons_non_prefixed_keyboard_key():
    yaml_blob = "---\nkeyboard:\n- - text: Join\n    callback_data: join:1\n"
    buttons = parse_buttons(yaml_blob)
    assert len(buttons.keyboard) == 1
    assert buttons.keyboard[0][0].text == "Join"
    assert buttons.keyboard[0][0].callback_data == "join:1"


def test_parse_location_invalid_yaml_raises_row_mapping_error():
    # Truly malformed YAML — safe_load raises YAMLError, mapper converts to RowMappingError.
    invalid_yaml = "name: [unclosed"
    with pytest.raises(RowMappingError):
        parse_location(invalid_yaml)


def test_parse_location_bad_coordinate_values_fall_back_to_none():
    # Coord values that cannot be cast to float should leave coordinates as None.
    yaml_blob = "---\n:name: Library\n:coord:\n- not-a-number\n- also-bad\n"
    location = parse_location(yaml_blob)
    assert location.name == "Library"
    assert location.coordinates is None
