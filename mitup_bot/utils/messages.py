CHARACTERS_TO_SCAPE = [".", "!",]


def sanitize_message(message: str) -> str:
    for character in CHARACTERS_TO_SCAPE:
        message = message.replace(character, f"\\{character}")
    return message
