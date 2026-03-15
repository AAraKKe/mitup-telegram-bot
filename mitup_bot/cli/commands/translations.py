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


def msgids_for_language(lang: str) -> set[str]:
    lines = po_file_for_language(lang).read_text(encoding="utf-8").splitlines()
    return {line.split(None, 1)[1].strip() for line in lines if line.startswith("msgid ")}


def parse_po_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def msgid_from_block(block: list[str]) -> str | None:
    for line in block:
        if line.startswith("msgid "):
            return line.split(None, 1)[1]
    return None


def filter_blocks(blocks: list[list[str]], english_msgids: set[str]) -> tuple[list[list[str]], list[str]]:
    kept: list[list[str]] = []
    removed_msgids: list[str] = []
    for block in blocks:
        msgid = msgid_from_block(block)
        if msgid is None or msgid == '""' or msgid in english_msgids:
            kept.append(block)
        else:
            removed_msgids.append(msgid)
    return kept, removed_msgids


def clean_all_locales() -> int:
    english_msgids = msgids_for_language("en")
    non_english_languages = [lang for lang in SUPPORTED_LANGUAGES if lang != "en"]

    had_error = False
    for lang in non_english_languages:
        po_path = po_file_for_language(lang)
        try:
            text = po_path.read_text(encoding="utf-8")
        except OSError as exc:
            error(f"{lang}: could not read {po_path}: {exc}")
            had_error = True
            continue

        kept, removed_msgids = filter_blocks(parse_po_blocks(text), english_msgids)

        if not removed_msgids:
            success(f"{lang}: already clean")
            continue

        cleaned = "\n\n".join("\n".join(block) for block in kept) + "\n"

        try:
            po_path.write_text(cleaned, encoding="utf-8")
        except OSError as exc:
            error(f"{lang}: could not write {po_path}: {exc}")
            had_error = True
            continue

        console().print(f"{lang}: removed {len(removed_msgids)} stale entry(s):")
        for msgid in removed_msgids:
            console().print(f"  [yellow]- {msgid.strip(chr(34))}[/]")

    return 1 if had_error else 0


def ensure_all_translations() -> int:
    console().print(f"\nValidating all PO files for languages {SUPPORTED_LANGUAGES}")

    english_msgids = msgids_for_language("en")
    non_english_languages = [lang for lang in SUPPORTED_LANGUAGES if lang != "en"]

    diverged = False

    for lang in non_english_languages:
        lang_msgids = msgids_for_language(lang)
        missing = sorted(english_msgids - lang_msgids)
        extra = sorted(lang_msgids - english_msgids)

        if not missing and not extra:
            success(f"{lang} is in sync with en")
            continue

        diverged = True
        if missing:
            error(f"{lang} is missing {len(missing)} msgid(s):")
            for msgid in missing:
                console().print(f"  [red]- {msgid.strip(chr(34))}[/]")
        if extra:
            error(f"{lang} has {len(extra)} stale msgid(s) (removed/renamed in English):")
            for msgid in extra:
                console().print(f"  [yellow]- {msgid.strip(chr(34))}[/]")

    if not diverged:
        success("All languages are in sync with English.")
        return 0

    return 1


@click.group()
def cli():
    """All commands related to translations management."""
    ...  # pragma: no cover


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
def clean_locales(ctx: click.Context):
    """Remove stale msgid blocks from non-English .po files."""
    ctx.exit(clean_all_locales())


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
