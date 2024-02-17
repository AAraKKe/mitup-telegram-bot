from mitup_bot.utils.messages import _sanitize


def test_sanitaze():
    message_point = _sanitize("Hello, World.")
    message_exclamation = _sanitize("Hello, World!")

    assert message_point == "Hello, World\\."
    assert message_exclamation == "Hello, World\\!"
    assert message_point != message_exclamation
    assert message_point != "Hello, World."
    assert message_exclamation != "Hello, World!"
