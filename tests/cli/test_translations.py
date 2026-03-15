import re
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from subprocess import CalledProcessError
from unittest import mock

import pytest
from click.testing import CliRunner
from rich.console import Console

from mitup_bot.cli.commands.translations import (
    METADATA,
    VALIDATE_PO_FILE,
    all_messages,
    build,
    clean_locales,
    mo_file_for_language,
    po_file_for_language,
    print_diff_line,
    update,
    validate_ids,
    validate_locales,
)
from mitup_bot.cli.helpers import console
from mitup_bot.translations import SUPPORTED_LANGUAGES
from tests.helpers import MITUP_DIR
from tests.helpers import console as test_console


@pytest.mark.parametrize(
    "validate, expected",
    [[True, lambda _: VALIDATE_PO_FILE], [False, lambda lang: MITUP_DIR / f"mitup_bot/locales/{lang}.po"]],
    ids=["validate", "no_validate"],
)
def test_po_file_for_language(lang: str, validate: bool, expected: Callable[[str], Path]):
    assert po_file_for_language(lang, validate) == expected(lang)


def test_mo_file_for_language(lang: str):
    assert mo_file_for_language(lang) == MITUP_DIR / f"mitup_bot/locales/{lang}/LC_MESSAGES/mitup_bot.mo"


@pytest.mark.parametrize(
    "line, expected",
    [
        ["- removed line", "\x1b[1;31m- removed line\x1b[0m"],
        ["+ added line", "\x1b[1;32m+ added line\x1b[0m"],
        ["same line", "same line"],
    ],
    ids=["removed", "added", "same"],
)
def test_print_diff(line: str, expected: str):
    with mock.patch("mitup_bot.cli.commands.translations.console") as console_mock:
        output = StringIO()
        tmp_console = Console(file=output, force_terminal=True)
        console_mock.return_value = tmp_console
        print_diff_line(line)

    assert expected == output.getvalue()


@mock.patch("mitup_bot.cli.commands.translations.mo_file_for_language")
@mock.patch("mitup_bot.cli.commands.translations.subprocess")
def test_build_skips_mkdir_when_mo_directory_already_exists(
    subprocess_mock: mock.Mock, mo_mock: mock.Mock, tmp_path: Path
):
    # Pre-create all LC_MESSAGES directories so mo_path.parent.exists() returns True
    for lang in SUPPORTED_LANGUAGES:
        (tmp_path / f"locales/{lang}/LC_MESSAGES").mkdir(parents=True)

    mo_mock.side_effect = lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"

    with console().capture() as capture:
        result = CliRunner().invoke(build)

    captured = capture.get()

    assert result.exit_code == 0
    # The "Creating mo path" message must NOT appear since the directories already exist
    assert "Creating mo path for language" not in captured


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_build_translations_fails_without_po_file(po_mock: mock.Mock):
    po_mock.return_value = Path("non_existent_file")
    with console().capture() as capture:
        result = CliRunner().invoke(build)

    assert result.exit_code == 1
    assert "ERROR: Po file non_existent_file does not exist." in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.mo_file_for_language")
@mock.patch("mitup_bot.cli.commands.translations.subprocess")
def test_build_creates_mo_directory(subprocess_mock: mock.Mock, mo_mock: mock.Mock, tmp_path: Path):
    mo_mock.side_effect = lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"

    with console().capture() as capture:
        result = CliRunner().invoke(build)

    captured = capture.get()

    assert result.exit_code == 0
    assert "Creating mo path for language en" in captured
    assert "Creating mo path for language es_ES" in captured

    assert (tmp_path / "locales/en/LC_MESSAGES").exists()
    assert (tmp_path / "locales/es_ES/LC_MESSAGES").exists()

    expected_calls = [
        mock.call.run(
            [
                "msgfmt",
                "-o",
                (tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo").absolute(),
                po_file_for_language(lang).absolute(),
            ],
            check=True,
        )
        for lang in SUPPORTED_LANGUAGES
    ]
    subprocess_mock.assert_has_calls(expected_calls, any_order=True)


@mock.patch("mitup_bot.cli.commands.translations.mo_file_for_language")
@mock.patch("mitup_bot.cli.commands.translations.subprocess")
def test_build_fails_if_subprocess_fails(subprocess_mock: mock.Mock, mo_mock: mock.Mock, tmp_path: Path):
    mo_mock.side_effect = lambda lang: tmp_path / f"locales/{lang}/LC_MESSAGES/mitup_bot.mo"
    subprocess_mock.run.side_effect = CalledProcessError(returncode=1, cmd="msgfmt")

    with console().capture() as capture:
        result = CliRunner().invoke(build)

    captured = capture.get()

    assert result.exit_code == 1
    assert (
        test_console.text_with_ansi_codes(
            f"[bold red]✘ Error compiling {po_file_for_language('en').absolute()}: "
            "Command 'msgfmt' returned non-zero exit status 1.[/]"
        )
        # Console can break long messages in separate lines
        in captured
    )


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_generate_translations(po_mock: mock.Mock, tmp_path: Path):
    po_mock.return_value = tmp_path / "en.po"

    CliRunner().invoke(update)

    assert po_mock.return_value.exists()

    with open(po_mock.return_value) as f:
        content = f.read()

        assert METADATA in content
        # There is a message string for every message in the source code (-1 because of the msgid in metadata)
        assert content.count("msgid") - 1 == sum(1 for messages in all_messages() for _ in messages)


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_validate_ids_failing(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"
    po_file.write_text("Some text")

    mock_po.side_effect = lambda lang, validate: VALIDATE_PO_FILE if validate else po_file

    with console().capture() as capture:
        result = CliRunner().invoke(validate_ids)

    assert result.exit_code == 1
    assert "Translations files are not up to date." in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_validate_ids_succeeds(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"

    mock_po.return_value = po_file

    with console().capture() as capture:
        result = CliRunner().invoke(validate_ids)

    assert result.exit_code == 0
    assert "Translations files are up to date." in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_validate_locales_succeeds(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"
    po_file.write_text('msgid: "test"\nmsgstr: "test"')

    mock_po.return_value = po_file

    with console().capture() as capture:
        result = CliRunner().invoke(validate_locales)

    assert result.exit_code == 0
    assert "All languages are in sync with English." in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_validate_locales_fails(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"
    # Use proper PO format (no colon after msgid) so msgids_for_language() parses them correctly
    po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')

    es_po_file = tmp_path / "es_ES.po"
    es_po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n\nmsgid "test2"\nmsgstr "test2"\n')

    mock_po.side_effect = lambda lang: es_po_file if lang == "es_ES" else po_file

    with console().capture() as capture:
        result = CliRunner().invoke(validate_locales)

    assert result.exit_code == 1
    # Strip ANSI escape codes before asserting: Rich injects color codes around numbers and
    # parentheses, which would break substring checks on the raw captured string.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", capture.get())
    assert "es_ES has 1 stale msgid(s) (removed/renamed in English):" in plain
    # msgid.strip(chr(34)) strips the surrounding quotes from the raw token "test2" -> test2
    assert "- test2" in plain


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_clean_locales_removes_stale_entries(mock_po: mock.Mock, tmp_path: Path):
    # en.po has one msgid; es_ES.po has two — "test2" is stale
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')

    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n\nmsgid "test2"\nmsgstr "prueba2"\n')

    mock_po.side_effect = lambda lang, validate=False: en_po if lang == "en" else es_po

    with console().capture() as capture:
        result = CliRunner().invoke(clean_locales)

    assert result.exit_code == 0
    # Strip ANSI escape codes: Rich colorizes numbers and parentheses, breaking raw substrings.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", capture.get())
    assert "removed 1 stale entry(s)" in plain
    content = es_po.read_text()
    assert "test2" not in content
    assert "test" in content


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_clean_locales_already_clean(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"
    po_file.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')

    mock_po.return_value = po_file

    with console().capture() as capture:
        result = CliRunner().invoke(clean_locales)

    assert result.exit_code == 0
    assert "already clean" in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_clean_locales_preserves_header(mock_po: mock.Mock, tmp_path: Path):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')

    es_po = tmp_path / "es_ES.po"
    es_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "prueba"\n\nmsgid "stale"\nmsgstr "viejo"\n')

    mock_po.side_effect = lambda lang, validate=False: en_po if lang == "en" else es_po

    CliRunner().invoke(clean_locales)

    content = es_po.read_text()
    assert 'msgid ""\nmsgstr ""' in content


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_clean_locales_read_error(mock_po: mock.Mock, tmp_path: Path):
    en_po = tmp_path / "en.po"
    en_po.write_text('msgid ""\nmsgstr ""\n\nmsgid "test"\nmsgstr "test"\n')

    bad_path = mock.Mock(spec=Path)
    bad_path.read_text.side_effect = OSError("Permission denied")

    mock_po.side_effect = lambda lang, validate=False: en_po if lang == "en" else bad_path

    with console().capture() as capture:
        result = CliRunner().invoke(clean_locales)

    assert result.exit_code == 1
    assert "could not read" in capture.get()
