import os
from pathlib import Path

from mb.locales import locales_stale
from mb.locales_ops import LOCALE_STAMP_NAME, po_content_hash


def write_catalog(
    locales_dir: Path, lang: str, *, po_text: str = 'msgid ""\nmsgstr ""\n', with_mo: bool = True
) -> Path:
    po_file = locales_dir / f"{lang}.po"
    po_file.write_text(po_text)
    if with_mo:
        mo_dir = locales_dir / lang / "LC_MESSAGES"
        mo_dir.mkdir(parents=True)
        (mo_dir / "mitup_bot.mo").write_bytes(b"")
    return po_file


def stamp(locales_dir: Path):
    """Record the current .po content hash, as compile_locales does after a successful build."""
    (locales_dir / LOCALE_STAMP_NAME).write_text(po_content_hash(locales_dir))


def test_empty_locales_dir_is_stale(tmp_path: Path):
    assert locales_stale(tmp_path) is True


def test_missing_mo_is_stale(tmp_path: Path):
    write_catalog(tmp_path, "en", with_mo=False)
    stamp(tmp_path)

    assert locales_stale(tmp_path) is True


def test_missing_stamp_is_stale(tmp_path: Path):
    write_catalog(tmp_path, "en")

    assert locales_stale(tmp_path) is True


def test_matching_stamp_is_fresh(tmp_path: Path):
    write_catalog(tmp_path, "en")
    stamp(tmp_path)

    assert locales_stale(tmp_path) is False


def test_changed_po_after_stamp_is_stale(tmp_path: Path):
    po_file = write_catalog(tmp_path, "en")
    stamp(tmp_path)
    po_file.write_text('msgid "hi"\nmsgstr "hi"\n')

    assert locales_stale(tmp_path) is True


def test_newer_po_mtime_alone_stays_fresh(tmp_path: Path):
    """The regression this mechanism fixes: a fresh git checkout restamps .po newer than .mo, but
    identical content must still count as fresh so CI does not rebuild every job."""
    po_file = write_catalog(tmp_path, "en")
    stamp(tmp_path)
    mo_file = tmp_path / "en" / "LC_MESSAGES" / "mitup_bot.mo"
    os.utime(mo_file, (100.0, 100.0))
    os.utime(po_file, (200.0, 200.0))

    assert locales_stale(tmp_path) is False


def test_one_changed_language_marks_everything_stale(tmp_path: Path):
    write_catalog(tmp_path, "en")
    es_po = write_catalog(tmp_path, "es_ES")
    stamp(tmp_path)
    es_po.write_text('msgid "hola"\nmsgstr "hola"\n')

    assert locales_stale(tmp_path) is True
