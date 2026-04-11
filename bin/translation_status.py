#!/usr/bin/env python3
"""
Report translation status for a single language.

Outputs a structured report showing:
- Missing msgid blocks (present in en.po but absent from the language file)
- Empty msgstr entries (msgid exists but msgstr is "")
- Updated English strings whose translations may be stale

Designed to be called by the translator agent so it gets all the
information it needs in one shot, without needing grep/awk/etc.

Usage:
    hatch run dev:python bin/translation_status.py <lang_code>
    hatch run dev:python bin/translation_status.py <lang_code> --review [<git_ref>]

Examples:
    hatch run dev:python bin/translation_status.py es_ES
    hatch run dev:python bin/translation_status.py de_DE --review
    hatch run dev:python bin/translation_status.py it_IT --review HEAD~5
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_po_entries(text: str) -> list[tuple[str, str]]:
    """Parse a .po file into (msgid, msgstr) pairs, skipping the header."""
    entries: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        if lines[i].startswith("msgid "):
            msgid = lines[i].split(None, 1)[1].strip('"')

            # Skip header block (empty msgid)
            if msgid == "":
                while i < len(lines) and lines[i].strip():
                    i += 1
                continue

            # Find corresponding msgstr
            msgstr = ""
            i += 1
            while i < len(lines) and not lines[i].startswith("msgstr "):
                i += 1
            if i < len(lines) and lines[i].startswith("msgstr "):
                msgstr = lines[i].split(None, 1)[1].strip('"')
            entries.append((msgid, msgstr))
        i += 1

    return entries


def get_old_en_entries(git_ref: str) -> dict[str, str] | None:
    """Get English entries from a previous git revision. Returns None on failure."""
    en_po_rel = str(TranslationEngine.LOCALES_DIR.relative_to(REPO_ROOT) / "en.po")
    try:
        result = subprocess.run(
            ["git", "show", f"{git_ref}:{en_po_rel}"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            return None
        return dict(parse_po_entries(result.stdout))
    except Exception:
        return None


def main() -> int:
    args = sys.argv[1:]

    if not args:
        print(f"Usage: {sys.argv[0]} <lang_code> [--review [<git_ref>]]")
        print(f"Supported languages: {', '.join(SUPPORTED_LANGUAGES)}")
        return 1

    lang = args[0]
    review_mode = "--review" in args
    git_ref = "main"  # default: compare against main branch

    if review_mode:
        review_idx = args.index("--review")
        if review_idx + 1 < len(args):
            git_ref = args[review_idx + 1]

    if lang not in SUPPORTED_LANGUAGES:
        print(f"Error: '{lang}' is not a supported language.")
        print(f"Supported: {', '.join(SUPPORTED_LANGUAGES)}")
        return 1

    if lang == "en":
        print("Error: English is the source language, not a translation target.")
        return 1

    locales_dir = TranslationEngine.LOCALES_DIR
    en_path = locales_dir / "en.po"
    lang_path = locales_dir / f"{lang}.po"

    if not en_path.exists():
        print(f"Error: English source file not found at {en_path}")
        return 1

    if not lang_path.exists():
        print(f"Error: Language file not found at {lang_path}")
        return 1

    # Parse both files
    en_entries = parse_po_entries(en_path.read_text(encoding="utf-8"))
    lang_entries = parse_po_entries(lang_path.read_text(encoding="utf-8"))

    en_dict = dict(en_entries)
    en_msgids = set(en_dict.keys())
    lang_dict = dict(lang_entries)
    lang_msgids = set(lang_dict.keys())

    # Compute sets
    missing_msgids = sorted(en_msgids - lang_msgids)
    empty_msgstr = sorted(msgid for msgid, msgstr in lang_entries if msgstr == "" and msgid in en_msgids)
    translated = sorted(msgid for msgid, msgstr in lang_entries if msgstr != "" and msgid in en_msgids)
    stale_msgids = sorted(lang_msgids - en_msgids)

    # Print report
    print(f"=== Translation status for {lang} ===")
    print(f"File: {lang_path}")
    print(f"Total English msgids: {len(en_msgids)}")
    print(f"Translated: {len(translated)}")
    print(f"Empty msgstr (needs translation): {len(empty_msgstr)}")
    print(f"Missing msgid blocks (needs adding): {len(missing_msgids)}")
    print(f"Stale msgids (removed from English): {len(stale_msgids)}")
    print()

    if missing_msgids:
        print("--- MISSING MSGID BLOCKS (add these to the .po file and translate) ---")
        for msgid in missing_msgids:
            en_text = en_dict.get(msgid, "")
            print(f'  msgid "{msgid}"')
            print(f'  English text: "{en_text}"')
            print()

    if empty_msgstr:
        print("--- EMPTY MSGSTR ENTRIES (translate these) ---")
        for msgid in empty_msgstr:
            en_text = en_dict.get(msgid, "")
            print(f'  msgid "{msgid}"')
            print(f'  English text: "{en_text}"')
            print()

    if stale_msgids:
        print("--- STALE MSGIDS (consider removing) ---")
        for msgid in stale_msgids:
            print(f'  msgid "{msgid}"')
        print()

    # Review mode: detect English strings that changed since git_ref
    if review_mode:
        old_en = get_old_en_entries(git_ref)
        if old_en is None:
            print(f"Warning: could not read en.po from git ref '{git_ref}'. Skipping review section.")
        else:
            changed: list[tuple[str, str, str]] = []  # (msgid, old_english, new_english)
            for msgid, new_text in en_dict.items():
                old_text = old_en.get(msgid)
                if old_text is not None and old_text != new_text:
                    changed.append((msgid, old_text, new_text))

            changed.sort(key=lambda x: x[0])

            if changed:
                print(f"--- UPDATED ENGLISH STRINGS (since {git_ref}) — review translations ---")
                for msgid, old_text, new_text in changed:
                    current_translation = lang_dict.get(msgid, "<missing>")
                    print(f'  msgid "{msgid}"')
                    print(f'  Old English:         "{old_text}"')
                    print(f'  New English:         "{new_text}"')
                    print(f'  Current translation: "{current_translation}"')
                    print()
            else:
                print(f"No English strings have changed since {git_ref}.")
        print()

    if not missing_msgids and not empty_msgstr and not review_mode:
        print("All entries are translated and in sync with English!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
