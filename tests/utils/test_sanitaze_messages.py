from mitup_bot.utils.messages import sanitize_message


def test_sanitaze_message():
    message_point = sanitize_message("Hello, World.")
    message_exclamation = sanitize_message("Hello, World!")

    assert message_point == "Hello, World\\."
    assert message_exclamation == "Hello, World\\!"
    assert message_point != message_exclamation
    assert message_point != "Hello, World."
    assert message_exclamation != "Hello, World!"
