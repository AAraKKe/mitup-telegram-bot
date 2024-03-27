from mitup_bot.utils.messages import MessageBase, _sanitize


def test_sanitIze():
    message_point = _sanitize("Hello, World.")
    message_exclamation = _sanitize("Hello, World!")

    assert message_point == "Hello, World\\."
    assert message_exclamation == "Hello, World\\!"
    assert message_point != message_exclamation
    assert message_point != "Hello, World."
    assert message_exclamation != "Hello, World!"


def test_sanitize_user_input():
    class TestMessage(MessageBase):
        TEST = "Hello, **$name!**. _This is cursive_"

    message = TestMessage.TEST.get(name="**New_World**")

    assert message == "Hello, **\\*\\*New\\_World\\*\\*\\!**\\. _This is cursive_"
