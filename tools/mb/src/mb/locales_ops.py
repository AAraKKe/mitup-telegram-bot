from __future__ import annotations

import datetime as dt
import difflib
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text

from mitup_bot.__about__ import __version__ as version
from mitup_bot.format_tags import FORMAT_TAG_NAMES, TOKEN_RE, placeholder_names
from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine

from . import console, runner

if TYPE_CHECKING:
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

# Name of the stamp file, co-located with the compiled catalogs, that records the content hash
# of the .po sources the catalogs were built from (see po_content_hash).
LOCALE_STAMP_NAME = ".po-content-hash"


def po_content_hash(locales_dir: Path) -> str:
    """Return a hash of every .po source in ``locales_dir``, keyed by filename.

    The compiled-catalog freshness check content-addresses the sources instead of comparing
    mtimes, because a fresh ``git checkout`` restamps the .po files newer than the .mo catalogs
    restored from the ``build-translations`` artifact. An mtime comparison would read that as
    stale and recompile the catalogs in every CI job; a content hash only changes when a .po
    actually changes.
    """
    digest = hashlib.sha256()
    for po_file in sorted(locales_dir.glob("*.po")):
        digest.update(po_file.name.encode())
        digest.update(b"\0")
        digest.update(po_file.read_bytes())
    return digest.hexdigest()


def po_file_for_language(lang: str, validate: bool = False) -> Path:
    return VALIDATE_PO_FILE if validate else TranslationEngine.LOCALES_DIR / f"{lang}.po"


def mo_file_for_language(lang: str) -> Path:
    return TranslationEngine.LOCALES_DIR / f"{lang}/LC_MESSAGES/{TranslationEngine.DOMAIN}.mo"


def all_messages() -> list[type[MessageBase]]:
    # Importing the module registers every MessageBase subclass; __subclasses__ then
    # sees them all. Kept lazy so plain `mb` invocations don't import the bot's copy.
    from mitup_bot.utils.messages import MessageBase

    return [cls for cls in MessageBase.__subclasses__() if cls != MessageBase]


def generate_translations(validate: bool):
    po_path = po_file_for_language("en", validate)

    with open(po_path, "w") as f:
        f.write(METADATA)

        f.write("\n\n#: libs/telegram/mitup_bot/utils/messages.py\n")
        for message_class in all_messages():
            for message in message_class:
                msgstr = repr(message.value)[1:-1].replace('"', r"\"")
                f.write(f'\nmsgid "{message.id()}"\n')
                f.write(f'msgstr "{msgstr}"\n')


def diff_report(diff_lines: list[str]) -> Text:
    report = Text()
    for line in diff_lines:
        if line.startswith("-"):
            report.append(line, style="bold red")
        elif line.startswith("+"):
            report.append(line, style="bold green")
        else:
            report.append(line)
    return report


def validate_translations() -> int:
    with open(po_file_for_language("en", False)) as f:
        real = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    with open(po_file_for_language("en", True)) as f:
        validate = [line for line in f.readlines() if "PO-Revision-Date" not in line and len(line) > 0]

    if diff := list(difflib.unified_diff(real, validate)):
        console.show(diff_report(diff))
        console.error("Translations files are not up to date.")
        return 1

    console.success("Translations files are up to date.")
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


def unquote_po_line(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2:
        return stripped[1:-1]
    return stripped


def msgstr_from_block(block: list[str]) -> str | None:
    """Return the translated text of *block*, gluing on any continuation lines that follow it.

    PO concatenates adjacent quoted lines with no separator, so a msgstr split across lines is one
    string and has to be read as one before its placeholders can be counted.
    """
    for index, line in enumerate(block):
        if not line.startswith("msgstr "):
            continue
        parts = [unquote_po_line(line.split(None, 1)[1])]
        parts.extend(unquote_po_line(rest) for rest in block[index + 1 :] if rest.strip().startswith('"'))
        return "".join(parts)
    return None


def entries_for_language(lang: str) -> dict[str, str]:
    """Return the msgid → msgstr mapping of a catalog, without its metadata header."""
    blocks = parse_po_blocks(po_file_for_language(lang).read_text(encoding="utf-8"))
    entries: dict[str, str] = {}
    for block in blocks:
        msgid = msgid_from_block(block)
        msgstr = msgstr_from_block(block)
        if msgid is None or msgid == '""' or msgstr is None:
            continue
        entries[msgid] = msgstr
    return entries


# One diverging entry: its msgid, the placeholders only English has, and the ones only it has.
PlaceholderMismatch = tuple[str, set[str], set[str]]


def placeholder_mismatches(english: dict[str, str], translated: dict[str, str]) -> list[PlaceholderMismatch]:
    """Every entry whose translation does not carry exactly the `${...}` placeholders English does.

    A placeholder is a contract between the catalog and the code that fills it: one dropped in
    translation leaves a gap where a name or a number should be, and one invented in translation
    ships to the reader as the literal `${...}` because nothing supplies it.

    An empty msgstr is not a translation at all. `msgfmt` leaves it out of the compiled catalog and
    the reader is served the English entry, so it carries English's placeholders by definition.
    """
    mismatches: list[PlaceholderMismatch] = []
    for msgid, source in english.items():
        if not (translation := translated.get(msgid, "")):
            continue
        expected = placeholder_names(source)
        found = placeholder_names(translation)
        if expected != found:
            mismatches.append((msgid, expected - found, found - expected))
    return sorted(mismatches, key=lambda mismatch: mismatch[0])


def tag_fault(text: str) -> str | None:
    """Describe the first formatting tag in *text* the renderer could not carry.

    Two ways a tag fails. A name outside the dialect has no markup to become, and a rich message
    carries its formatting as nested HTML, where a close only ever pairs with the tag opened most
    recently, so an unclosed or crossing tag has no rendering either. Once one tag is out of place
    the rest of the string cannot be read reliably, which is why only the first is described rather
    than every tag the mistake knocks out of step.
    """
    open_tags: list[str] = []
    for token in TOKEN_RE.finditer(text):
        if token.group("var") is not None:
            continue
        tag = token.group("tag")
        if tag not in FORMAT_TAG_NAMES:
            return f"<{tag}> is not a tag the dialect knows"
        if not token.group("close"):
            open_tags.append(tag)
            continue
        if not open_tags:
            return f"</{tag}> closes a tag that was never opened"
        if open_tags[-1] != tag:
            return f"</{tag}> crosses <{open_tags[-1]}>, which is still open"
        open_tags.pop()
    if open_tags:
        return f"<{open_tags[-1]}> is never closed"
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
            console.error(f"{lang}: could not read {po_path}: {exc}")
            had_error = True
            continue

        kept, removed_msgids = filter_blocks(parse_po_blocks(text), english_msgids)

        if not removed_msgids:
            console.success(f"{lang}: already clean")
            continue

        cleaned = "\n\n".join("\n".join(block) for block in kept) + "\n"

        try:
            po_path.write_text(cleaned, encoding="utf-8")
        except OSError as exc:
            console.error(f"{lang}: could not write {po_path}: {exc}")
            had_error = True
            continue

        console.info(f"{lang}: removed {len(removed_msgids)} stale entry(s):")
        for msgid in removed_msgids:
            console.info(f"  [yellow]- {msgid.strip(chr(34))}[/]")

    return 1 if had_error else 0


def report_msgid_divergence(lang: str, english_msgids: set[str]) -> bool:
    lang_msgids = msgids_for_language(lang)
    missing = sorted(english_msgids - lang_msgids)
    extra = sorted(lang_msgids - english_msgids)

    if missing:
        console.error(f"{lang} is missing {len(missing)} msgid(s):")
        for msgid in missing:
            console.info(f"  [red]- {msgid.strip(chr(34))}[/]")
    if extra:
        console.error(f"{lang} has {len(extra)} stale msgid(s) (removed/renamed in English):")
        for msgid in extra:
            console.info(f"  [yellow]- {msgid.strip(chr(34))}[/]")
    return bool(missing or extra)


def report_tag_faults(lang: str, entries: dict[str, str]) -> bool:
    faults = sorted((msgid, problem) for msgid, text in entries.items() if (problem := tag_fault(text)) is not None)
    if not faults:
        return False

    console.error(f"{lang} has {len(faults)} entry(s) with unusable formatting tags:")
    for msgid, problem in faults:
        console.info(f"  [red]- {msgid.strip(chr(34))}[/]: {problem}")
    return True


def report_placeholder_divergence(lang: str, english_entries: dict[str, str], lang_entries: dict[str, str]) -> bool:
    mismatches = placeholder_mismatches(english_entries, lang_entries)
    if not mismatches:
        return False

    console.error(f"{lang} has {len(mismatches)} entry(s) with mismatched placeholders:")
    for msgid, missing, extra in mismatches:
        console.info(f"  [red]- {msgid.strip(chr(34))}[/]")
        if missing:
            console.info(f"      missing: {', '.join(sorted(missing))}")
        if extra:
            console.info(f"      unexpected: {', '.join(sorted(extra))}")
    return True


def ensure_all_translations() -> int:
    console.info(f"\nValidating all PO files for languages {SUPPORTED_LANGUAGES}")

    english_msgids = msgids_for_language("en")
    english_entries = entries_for_language("en")
    non_english_languages = [lang for lang in SUPPORTED_LANGUAGES if lang != "en"]

    # English is checked for tag balance too: it is a catalog like any other, and an unclosed tag
    # authored in `messages.py` reaches the most readers of all.
    diverged = report_tag_faults("en", english_entries)

    for lang in non_english_languages:
        lang_entries = entries_for_language(lang)
        lang_diverged = report_msgid_divergence(lang, english_msgids)
        lang_diverged |= report_placeholder_divergence(lang, english_entries, lang_entries)
        lang_diverged |= report_tag_faults(lang, lang_entries)
        if not lang_diverged:
            console.success(f"{lang} is in sync with en")
        diverged |= lang_diverged

    if not diverged:
        console.success("All languages are in sync with English.")
        return 0

    return 1


def compile_locales() -> int:
    for lang in SUPPORTED_LANGUAGES:
        po_path = po_file_for_language(lang)
        mo_path = mo_file_for_language(lang)

        if not po_path.exists():
            console.error(f"Po file {po_path} does not exist.")
            return 1

        if not mo_path.parent.exists():
            console.info(f"Creating mo path for language {lang}")
            mo_path.parent.mkdir(parents=True)

        console.info(f"Compiling {lang!r} mo file...")
        exit_code = runner.run_command(["msgfmt", "-o", str(mo_path.absolute()), str(po_path.absolute())])
        if exit_code != 0:
            console.error(f"Error compiling {po_path} (exit code {exit_code}).")
            return 1

        console.success(f"Successfully compiled {po_path} to {mo_path}")

    # Stamp the sources the catalogs were just built from so a later freshness check can skip the
    # rebuild without depending on mtimes (which a fresh git checkout invalidates).
    stamp_path = TranslationEngine.LOCALES_DIR / LOCALE_STAMP_NAME
    stamp_path.write_text(po_content_hash(TranslationEngine.LOCALES_DIR))
    return 0
