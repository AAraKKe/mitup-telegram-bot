from typing import cast

from mitup_bot import db
from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import MessageButtons
from mitup_bot.utils.entities import FormattedText

# A realistic `messages.buttons` row: a nested CallbackData object, a switch_inline_query
# share button, and a legacy string callback_data. This literal pins the persisted JSON wire
# format — if a change to ButtonConfig or MessageButtons breaks these assertions, it breaks
# every keyboard already stored in the database.
STORED_BUTTONS_JSON = (
    '{"keyboard":[['
    '{"text":"Join","callback_data":{"entity":"meeting","action":"join","id":42},'
    '"switch_inline_query":null,"switch_inline_query_current_chat":null,"url":null},'
    '{"text":"Share","callback_data":null,"switch_inline_query":"42",'
    '"switch_inline_query_current_chat":null,"url":null}],'
    '[{"text":"Edit","callback_data":"edit;meeting:42",'
    '"switch_inline_query":null,"switch_inline_query_current_chat":null,"url":null}]]}'
)


def stored_buttons() -> MessageButtons:
    return MessageButtons(
        keyboard=[
            [
                ButtonConfig(text="Join", callback_data=CallbackData(entity="meeting", action="join", id=42)),
                ButtonConfig(text="Share", switch_inline_query="42"),
            ],
            [ButtonConfig(text="Edit", callback_data="edit;meeting:42")],
        ]
    )


def test_stored_buttons_json_round_trips_byte_for_byte():
    restored = MessageButtons.model_validate_json(STORED_BUTTONS_JSON)

    assert restored.model_dump_json() == STORED_BUTTONS_JSON


def test_stored_buttons_json_validates_to_expected_objects():
    assert MessageButtons.model_validate_json(STORED_BUTTONS_JSON) == stored_buttons()


def test_buttons_serialize_to_stored_json():
    assert db.serialize_pydantic_model(stored_buttons()) == STORED_BUTTONS_JSON


def test_formatted_text_serializes_as_plain_text():
    # cast: deliberately passing a non-str to exercise the duck-typed before-validator.
    join_label = cast("str", FormattedText("Join"))
    buttons = MessageButtons(keyboard=[[ButtonConfig(text=join_label, callback_data="join;meeting:42")]])

    assert (
        buttons.model_dump_json() == '{"keyboard":[[{"text":"Join","callback_data":"join;meeting:42",'
        '"switch_inline_query":null,"switch_inline_query_current_chat":null,"url":null}]]}'
    )


def test_engine_deserializer_resolves_message_buttons():
    assert db.deserialize_pydantic_model(STORED_BUTTONS_JSON) == stored_buttons()
