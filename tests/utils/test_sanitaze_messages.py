from typing import override

import pytest

from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import MessageBase, TranslationEngineProtocol


class TestEngine(TranslationEngine):
    @override
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str:
        return "Hello, <b>${name}!</b>. <i>This is cursive</i>"


class TestMessage(MessageBase):
    TEST = ""  # Not matter the text since we are translating later

    def translations_class(self) -> type[TranslationEngineProtocol]:
        return TestEngine


def test_get_returns_formatted_text():

    result = TestMessage.TEST.get(name="World")
    assert result.text == "Hello, World!. This is cursive"
    assert len(result.entities) == 2
    assert result.entities[0].type == "bold"
    assert result.entities[1].type == "italic"


def test_get_returns_plain_text():
    class TestEngine2(TranslationEngine):
        @override
        @classmethod
        def translate(cls, message_id: str, lang: str) -> str:
            return "Hello, world!"

    class TestMessage2(MessageBase):
        TEST = ""  # Not matter the text since we are translating later

        def translations_class(self) -> type[TranslationEngineProtocol]:
            return TestEngine2

    result = TestMessage2.TEST.get(name="World")
    assert result == FormattedText("Hello, world!")


def test_get_text_raises_error_if_entities_are_present():
    with pytest.raises(ValueError):
        TestMessage.TEST.get_text(name="World")


def test_get_accepts_formatted_text_kwarg_and_preserves_entities():
    from telegram import MessageEntity

    italic_name = FormattedText("Alice", [MessageEntity(type="italic", offset=0, length=5)])
    result = TestMessage.TEST.get(name=italic_name)
    # Plain text: bold wraps "Alice!" and italic comes from the substituted FormattedText
    assert result.text == "Hello, Alice!. This is cursive"
    italic_entities = [e for e in result.entities if e.type == "italic"]
    bold_entities = [e for e in result.entities if e.type == "bold"]
    # The outer <b>...</b> tag from the template produces a bold entity
    assert len(bold_entities) == 1
    assert bold_entities[0].offset == 7  # "Hello, " = 7
    # The italic entity from the FormattedText kwarg is also present
    assert any(e.type == "italic" and e.offset == 7 and e.length == 5 for e in italic_entities)
