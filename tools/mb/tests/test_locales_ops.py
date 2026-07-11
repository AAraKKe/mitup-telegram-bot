from pathlib import Path
from unittest import mock

import pytest
from command_recording import CommandRecorder
from mb import console, locales_ops
from mb.locales import app
from typer.testing import CliRunner

from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine

cli = CliRunner()

LOCALES_DIR = TranslationEngine.LOCALES_DIR


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


@pytest.mark.parametrize(
    "validate, expected",
    [
        (True, locales_ops.VALIDATE_PO_FILE),
        (False, LOCALES_DIR / "es_ES.po"),
    ],
    ids=["validate", "no_validate"],
)
def test_po_file_for_language(validate: bool, expected: Path):
    assert locales_ops.po_file_for_language("es_ES", validate) == expected


def test_mo_file_for_language():
    assert locales_ops.mo_file_for_language("es_ES") == LOCALES_DIR / "es_ES/LC_MESSAGES/mitup_bot.mo"


def test_generate_translations_covers_every_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_file = tmp_path / "en.po"
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: po_file)

    locales_ops.generate_translations(validate=False)

    content = po_file.read_text()
    assert locales_ops.METADATA in content
    # One msgid per message plus the metadata header msgid.
    assert content.count("msgid") - 1 == sum(1 for messages in locales_ops.all_messages() for _ in messages)


def test_validate_translations_succeeds_when_in_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_file = tmp_path / "en.po"
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: po_file)
    locales_ops.generate_translations(validate=False)

    assert locales_ops.validate_translations() == 0


def test_validate_translations_fails_when_out_of_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    real_po = tmp_path / "en.po"
    real_po.write_text("Some text")
    validate_po = tmp_path / "validate.po"

    monkeypatch.setattr(
        locales_ops, "po_file_for_language", lambda lang, validate=False: validate_po if validate else real_po
    )
    locales_ops.generate_translations(validate=True)

    assert locales_ops.validate_translations() == 1
    assert "Translations files are not up to date." in combined(capsys)


def test_validate_ids_command_regenerates_and_validates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_file = tmp_path / "en.po"
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: po_file)

    result = cli.invoke(app, ["validate-ids"])

    assert result.exit_code == 0


def test_update_source_command_writes_english_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    po_file = tmp_path / "en.po"
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: po_file)

    result = cli.invoke(app, ["update-source"])

    assert result.exit_code == 0
    assert po_file.exists()


def test_msgid_from_block_returns_none_without_msgid():
    assert locales_ops.msgid_from_block(["# comment", 'msgstr "value"']) is None


def test_msgid_from_block_returns_msgid_when_present():
    assert locales_ops.msgid_from_block(['msgid "hello"', 'msgstr "world"']) == '"hello"'


def test_parse_po_blocks_ignores_consecutive_blank_lines():
    blocks = locales_ops.parse_po_blocks('msgid "a"\nmsgstr "b"\n\n\n\nmsgid "c"\nmsgstr "d"')
    assert blocks == [['msgid "a"', 'msgstr "b"'], ['msgid "c"', 'msgstr "d"']]


def test_parse_po_blocks_captures_trailing_block():
    blocks = locales_ops.parse_po_blocks('msgid "a"\nmsgstr "b"\n\nmsgid "c"\nmsgstr "d"')
    assert blocks[1] == ['msgid "c"', 'msgstr "d"']


@pytest.mark.parametrize("text", ["", "\n", "\n\n\n"], ids=["empty", "single_newline", "only_newlines"])
def test_parse_po_blocks_empty_input(text: str):
    assert locales_ops.parse_po_blocks(text) == []


def test_clean_locales_removes_stale_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n\nmsgid "test2"\nmsgstr "prueba2"\n')

    monkeypatch.setattr(
        locales_ops, "po_file_for_language", lambda lang, validate=False: en_po if lang == "en" else es_po
    )

    assert locales_ops.clean_all_locales() == 0
    output = combined(capsys)
    assert "removed 1 stale entry(s)" in output
    content = es_po.read_text()
    assert "test2" not in content
    assert "test" in content


def test_clean_locales_already_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    po_file = tmp_path / "en.po"
    po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: po_file)

    assert locales_ops.clean_all_locales() == 0
    assert "already clean" in combined(capsys)


def test_clean_locales_preserves_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n\nmsgid "stale"\nmsgstr "viejo"\n')

    monkeypatch.setattr(
        locales_ops, "po_file_for_language", lambda lang, validate=False: en_po if lang == "en" else es_po
    )

    locales_ops.clean_all_locales()

    assert 'msgid ""\nmsgstr ""' in es_po.read_text()


def test_clean_locales_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    bad_path = mock.Mock(spec=Path)
    bad_path.read_text.side_effect = OSError("Permission denied")

    monkeypatch.setattr(
        locales_ops, "po_file_for_language", lambda lang, validate=False: en_po if lang == "en" else bad_path
    )

    assert locales_ops.clean_all_locales() == 1
    assert "could not read" in combined(capsys)


def test_clean_locales_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    bad_path = mock.Mock(spec=Path)
    bad_path.read_text.return_value = (
        'msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n\nmsgid "stale"\nmsgstr "viejo"\n'
    )
    bad_path.write_text.side_effect = OSError("Permission denied")

    monkeypatch.setattr(
        locales_ops, "po_file_for_language", lambda lang, validate=False: en_po if lang == "en" else bad_path
    )

    assert locales_ops.clean_all_locales() == 1
    assert "could not write" in combined(capsys)


def test_ensure_all_translations_reports_stale_msgids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')
    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n\nmsgid "test2"\nmsgstr "test2"\n')

    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: es_po if lang == "es_ES" else en_po)

    assert locales_ops.ensure_all_translations() == 1
    output = combined(capsys)
    assert "es_ES has 1 stale msgid(s) (removed/renamed in English):" in output
    assert "- test2" in output


def test_ensure_all_translations_reports_missing_msgids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n\nmsgid "extra_en"\nmsgstr "extra"\n')
    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n')

    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang: es_po if lang == "es_ES" else en_po)

    assert locales_ops.ensure_all_translations() == 1
    output = combined(capsys)
    assert "es_ES is missing" in output
    assert "extra_en" in output


def test_compile_locales_creates_mo_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        locales_ops, "mo_file_for_language", lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"
    )

    assert locales_ops.compile_locales() == 0
    output = combined(capsys)
    assert "Creating mo path for language en" in output
    assert (tmp_path / "locales/en/LC_MESSAGES").exists()
    # One msgfmt invocation per supported language.
    assert len(recorder.commands) == len(SUPPORTED_LANGUAGES)
    assert recorder.commands[0][0] == "msgfmt"


def test_compile_locales_skips_mkdir_when_directory_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]
):
    for lang in SUPPORTED_LANGUAGES:
        (tmp_path / f"locales/{lang}/LC_MESSAGES").mkdir(parents=True)
    monkeypatch.setattr(
        locales_ops, "mo_file_for_language", lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"
    )

    assert locales_ops.compile_locales() == 0
    assert "Creating mo path for language" not in combined(capsys)


def test_compile_locales_fails_without_po_file(
    monkeypatch: pytest.MonkeyPatch, recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(locales_ops, "po_file_for_language", lambda lang, validate=False: Path("non_existent_file"))

    assert locales_ops.compile_locales() == 1
    assert "Po file non_existent_file does not exist." in combined(capsys)


def test_sync_stops_at_first_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    from mb import locales as locales_module

    monkeypatch.setattr(locales_module, "update_source_catalog", lambda: 0)
    monkeypatch.setattr(locales_ops, "clean_all_locales", lambda: 4)
    reached_compile = mock.Mock()
    monkeypatch.setattr(locales_ops, "compile_locales", reached_compile)

    result = cli.invoke(app, ["sync"])

    assert result.exit_code == 4
    reached_compile.assert_not_called()


def test_compile_locales_fails_when_msgfmt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: CommandRecorder, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        locales_ops, "mo_file_for_language", lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"
    )
    recorder.exit_codes["msgfmt"] = 1

    assert locales_ops.compile_locales() == 1
    assert "Error compiling" in combined(capsys)
