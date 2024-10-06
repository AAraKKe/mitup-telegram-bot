import datetime as dt
import difflib
import shutil
from pathlib import Path

import click

from mitup_bot.__about__ import __version__ as version
from mitup_bot.cli.helpers import console, error, success
from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine
from mitup_bot.utils.messages import (
    ButtonMessages,
    MeetingMessages,
    Messages,
    Month,
    MonthShort,
    SettingsMessages,
    Weekday,
)

METADATA = f"""
# MitupBot translations files.
# Copyright (C) 2024
# This file is distributed under the same license as the mitup_telegram_bot package.
# J. P. Araque, 2024.
# E. Araque, 2024.

msgid ""
msgstr ""
"Project-Id-Version: {TranslationEngine.DOMAIN} {version}\\n"
"Report-Msgid-Bugs-To: https://gitlab.com/meetupbot/mitup-telegram-bot/issues/new\\n"
"POT-Creation-Date: 2024-10-05 16:34+0100\\n"
"PO-Revision-Date: {dt.datetime.now().strftime("%Y-%m-%d %H:%M%z")}\\n"
"Language: en\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"""


def generate_translations(validate: bool):
    messages = [ButtonMessages, Messages, SettingsMessages, MeetingMessages, Weekday, Month, MonthShort]

    po_path = (
        Path("validate.po")
        if validate
        else TranslationEngine.LOCALES_DIR / f"en/LC_MESSAGES/{TranslationEngine.DOMAIN}.po"
    )

    with open(po_path, "w") as f:
        f.write(METADATA)

        f.write("\n\n#: mitup_bot/utils/messages.py\n")
        for message_class in messages:
            for message in message_class:
                msgstr = repr(message.value)[1:-1].replace('"', r"\"")
                f.write(f'\nmsgid "{message.id()}"\n')
                f.write(f'msgstr "{msgstr}"\n')

    if validate:
        return

    shutil.copy(po_path, TranslationEngine.LOCALES_DIR / f"{TranslationEngine.DOMAIN}.pot")


def print_diff_line(line: str):
    if line.startswith("-"):
        console.print(f"[red bold]{line}[/]", end="")
    elif line.startswith("+"):
        console.print(f"[green bold]{line}[/]", end="")
    else:
        console.print(line, end="")


def validate_translations() -> int:
    # Load both po files, the real one and the one created for validation and using difflib
    # diff both and find the differences

    with open(TranslationEngine.LOCALES_DIR / f"en/LC_MESSAGES/{TranslationEngine.DOMAIN}.po") as f:
        real = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    with open("validate.po") as f:
        validate = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    if diff := list(difflib.unified_diff(real, validate)):
        for line in diff:
            print_diff_line(line)

        error("Translations files are not up to date.")
        return 1

    success("Translations files are up to date.")
    return 0


def validate_locales() -> int:
    """Validate that all po files containe the same msgids."""
    console.print(f"\nValidating all PO files for languages {SUPPORTED_LANGUAGES}")

    msgdis: dict[str, list[str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        with open(TranslationEngine.LOCALES_DIR / f"{lang}/LC_MESSAGES/{TranslationEngine.DOMAIN}.po") as f:
            msgids = [line.split()[1] + "\n" for line in f.readlines() if line.startswith("msgid")]
            msgdis[lang] = msgids

    failed = False

    for idx, lang in enumerate(SUPPORTED_LANGUAGES[:-1]):
        for other_lang in SUPPORTED_LANGUAGES[idx + 1 :]:
            if diff := list(
                difflib.unified_diff(msgdis[lang], msgdis[other_lang], fromfile=lang, tofile=other_lang, n=0)
            ):
                error(f"Language {lang} and {other_lang} have different msgids.")
                for element in diff:
                    print_diff_line(element)
                failed = True

    if not failed:
        success("All languages have the same msgids.")
        return 0

    return 1


@click.command()
@click.option(
    "--validate", is_flag=True, default=False, help="Validate translations showing if there is any difference."
)
@click.pass_context
def cli(ctx: click.Context, validate: bool):
    """
    Generate translation files for English and POT file. If --validate is provided the files are not generated.
    Instead, the existing one will be validated showing if there is any difference with the latest values of the
    strings in the code. This will also validate that all supported languages have the same messages available.
    """
    generate_translations(validate)

    if not validate:
        success("Transations files generated successfully.")
        return 0

    return_code = validate_translations()
    return_code += validate_locales()

    ctx.exit(return_code)
