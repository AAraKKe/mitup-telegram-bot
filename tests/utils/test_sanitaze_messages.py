from typing import override

from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import MessageBase, TranslationEngineProtocol, sanitize


def test_sanitIze():
    message_point = sanitize("Hello, World.")
    message_exclamation = sanitize("Hello, World!")

    assert message_point == "Hello, World\\."
    assert message_exclamation == "Hello, World\\!"
    assert message_point != message_exclamation
    assert message_point != "Hello, World."
    assert message_exclamation != "Hello, World!"


def test_sanitize_user_input():
    class TestEngine(TranslationEngine):
        @override
        @classmethod
        def translate(cls, message_id: str, lang: str) -> str:
            return "Hello, **$name!**. _This is cursive_"

    class TestMessage(MessageBase):
        TEST = "Hello, **$name!**. _This is cursive_"

        def translations_class(self) -> type[TranslationEngineProtocol]:
            return TestEngine

    message = TestMessage.TEST.get(name="**New_World**")
    message_without_full_scape = TestMessage.TEST.get(full=False, name="_My World_")

    assert message == "Hello, **\\*\\*New\\_World\\*\\*\\!**\\. _This is cursive_"
    assert message_without_full_scape == "Hello, **_My World_\\!**\\. _This is cursive_"
