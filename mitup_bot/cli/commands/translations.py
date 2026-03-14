import datetime as dt
import difflib
import subprocess
from pathlib import Path

import click

from mitup_bot.__about__ import __version__ as version
from mitup_bot.cli.helpers import console, error, success
from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine
from mitup_bot.utils.messages import MessageBase

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
"X-Crowdin-SourceKey: msgstr\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"""

VALIDATE_PO_FILE = Path("validate.po")
POT_FILE = TranslationEngine.LOCALES_DIR / f"{TranslationEngine.DOMAIN}.pot"


def po_file_for_language(lang: str, validate: bool = False) -> Path:
    return VALIDATE_PO_FILE if validate else TranslationEngine.LOCALES_DIR / f"{lang}.po"


def mo_file_for_language(lang: str) -> Path:
    return TranslationEngine.LOCALES_DIR / f"{lang}/LC_MESSAGES/{TranslationEngine.DOMAIN}.mo"


def all_messages() -> list[type[MessageBase]]:
    # Get all classes from translations that are of type MessageBase
    return [cls for cls in MessageBase.__subclasses__() if cls != MessageBase]


def generate_translations(validate: bool):
    po_path = po_file_for_language("en", validate)

    with open(po_path, "w") as f:
        f.write(METADATA)

        f.write("\n\n#: mitup_bot/utils/messages.py\n")
        for message_class in all_messages():
            for message in message_class:
                msgstr = repr(message.value)[1:-1].replace('"', r"\"")
                f.write(f'\nmsgid "{message.id()}"\n')
                f.write(f'msgstr "{msgstr}"\n')


def print_diff_line(line: str):
    if line.startswith("-"):
        console().print(f"[red bold]{line}[/]", end="")
    elif line.startswith("+"):
        console().print(f"[green bold]{line}[/]", end="")
    else:
        console().print(line, end="")


def validate_translations() -> int:
    with open(po_file_for_language("en", False)) as f:
        real = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    with open(po_file_for_language("en", True)) as f:
        validate = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    if diff := list(difflib.unified_diff(real, validate)):
        for line in diff:
            print_diff_line(line)

        error("Translations files are not up to date.")
        return 1

    success("Translations files are up to date.")
    return 0


def ensure_all_translations() -> int:
    console().print(f"\nValidating all PO files for languages {SUPPORTED_LANGUAGES}")

    msgdis: dict[str, list[str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        with open(po_file_for_language(lang)) as f:
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


@click.group()
def cli():
    """All commands related to translations management."""
    pass  # pragma: no cover


@cli.command()
def update():
    """Update the English translation file with every message available in mitup_bot.utils.messages"""
    generate_translations(False)
    success("English file updated successfully.")


@cli.command()
@click.pass_context
def validate_ids(ctx: click.Context):
    """Ensure that all messages are present in the source translation file."""
    generate_translations(True)
    ctx.exit(validate_translations())


@cli.command()
@click.pass_context
def validate_locales(ctx: click.Context):
    """Validate that all po files contain the same msgids."""
    ctx.exit(ensure_all_translations())


@cli.command()
@click.pass_context
def build(ctx: click.Context):
    """Build all translations files for the bot."""
    for lang in SUPPORTED_LANGUAGES:
        po_path = po_file_for_language(lang)
        mo_path = mo_file_for_language(lang)

        if not po_path.exists():
            error(f"ERROR: Po file {po_path} does not exist.")
            ctx.exit(1)

        if not mo_path.parent.exists():
            console().print(f"Creating mo path for language {lang}")
            mo_path.parent.mkdir(parents=True)

        # Compile po file
        console().print(f"Compiling {lang!r} mo file...")

        try:
            subprocess.run(["msgfmt", "-o", mo_path.absolute(), po_path.absolute()], check=True)
            success(f"Successfully compiled {po_path} to {mo_path}")
        except Exception as e:
            error(f"Error compiling {po_path}: {e}")
            ctx.exit(1)
