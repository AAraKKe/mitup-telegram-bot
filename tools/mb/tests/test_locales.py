import os
from pathlib import Path

from mb.locales import locales_stale


def write_catalog(locales_dir: Path, lang: str, *, po_mtime: float, mo_mtime: float | None) -> Path:
    po_file = locales_dir / f"{lang}.po"
    po_file.write_text('msgid ""\nmsgstr ""\n')
    os.utime(po_file, (po_mtime, po_mtime))
    if mo_mtime is not None:
        mo_dir = locales_dir / lang / "LC_MESSAGES"
        mo_dir.mkdir(parents=True)
        mo_file = mo_dir / "mitup_bot.mo"
        mo_file.write_bytes(b"")
        os.utime(mo_file, (mo_mtime, mo_mtime))
    return po_file


def test_empty_locales_dir_is_stale(tmp_path: Path):
    assert locales_stale(tmp_path) is True


def test_missing_mo_is_stale(tmp_path: Path):
    write_catalog(tmp_path, "en", po_mtime=100.0, mo_mtime=None)

    assert locales_stale(tmp_path) is True


def test_mo_older_than_po_is_stale(tmp_path: Path):
    write_catalog(tmp_path, "en", po_mtime=200.0, mo_mtime=100.0)

    assert locales_stale(tmp_path) is True


def test_mo_newer_than_po_is_fresh(tmp_path: Path):
    write_catalog(tmp_path, "en", po_mtime=100.0, mo_mtime=200.0)

    assert locales_stale(tmp_path) is False


def test_one_stale_language_marks_everything_stale(tmp_path: Path):
    write_catalog(tmp_path, "en", po_mtime=100.0, mo_mtime=200.0)
    write_catalog(tmp_path, "es_ES", po_mtime=300.0, mo_mtime=200.0)

    assert locales_stale(tmp_path) is True
