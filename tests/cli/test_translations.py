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
            f"[bold red]✘ Error compiling {po_file_for_language("en").absolute()}: "
            "Command 'msgfmt' returned non-zero exit status 1.[/]"
        )
        # Console can break long messages in separate lines
        in captured
    )


@pytest.mark.parametrize(
    "validate, po_path",
    [[True, Path("validate.po")], [False, Path("en.po")]],
    ids=["validate", "no_validate"],
)
@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_generate_translations(po_mock: mock.Mock, validate: bool, po_path: Path, tmp_path: Path):
    po_mock.return_value = tmp_path / po_path

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
    assert "All languages have the same msgids." in capture.get()


@mock.patch("mitup_bot.cli.commands.translations.po_file_for_language")
def test_validate_locales_fails(mock_po: mock.Mock, tmp_path: Path):
    po_file = tmp_path / "en.po"
    po_file.write_text('msgid: "test"\nmsgstr: "test"')

    es_po_file = tmp_path / "es_ES.po"
    es_po_file.write_text('msgid: "test"\nmsgstr: "test"\nmsgid: "test2"\nmsgstr: "test2"')

    mock_po.side_effect = lambda lang: es_po_file if lang == "es_ES" else po_file

    with console().capture() as capture:
        result = CliRunner().invoke(validate_locales)

    assert result.exit_code == 1
    assert "Language en and es_ES have different msgids." in capture.get()
