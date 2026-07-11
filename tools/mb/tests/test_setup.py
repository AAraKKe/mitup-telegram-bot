import tomllib
from pathlib import Path
from unittest import mock

import pytest
import typer
from command_recording import CommandRecorder
from mb import console, setup_env
from mb.main import app
from typer.testing import CliRunner

from mitup_bot.config import Config, Env, TomlConfigProvider

cli = CliRunner()

TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


@pytest.fixture
def dev_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "dev.toml"
    monkeypatch.setattr(setup_env, "dev_toml_path", lambda: target)
    return target


def load_config(config_dir: Path) -> Config:
    """Load the generated file through the real provider chain — the drift gate.

    `TomlConfigProvider` resolves `environments/dev.toml` via `importlib.resources.files`,
    so pointing `files` at the temp directory makes it read the file just written.
    """
    with mock.patch("mitup_bot.config.files", return_value=config_dir):
        return Config.from_providers(TomlConfigProvider(Env.DEV))


def test_generated_config_validates(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)

    config = load_config(dev_toml.parent)
    assert config.bot.token.get_secret_value() == TOKEN
    assert config.db.engine_echo is True


def test_generated_sections_cover_required_config_sections(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)

    generated_sections = set(tomllib.loads(dev_toml.read_text()))
    required_sections = {name for name, field in Config.model_fields.items() if field.is_required()}
    assert required_sections <= generated_sections


def test_token_is_substituted(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)

    written = dev_toml.read_text()
    assert f'token = "{TOKEN}"' in written
    assert "${" not in written


def test_existing_file_declined_prompt_leaves_it_untouched(dev_toml: Path, monkeypatch: pytest.MonkeyPatch):
    dev_toml.write_text("customized = true\n")
    monkeypatch.setattr(typer, "confirm", mock.Mock(side_effect=typer.Abort()))

    with pytest.raises(typer.Abort):
        setup_env.write_dev_config(TOKEN, (), force=False)

    assert dev_toml.read_text() == "customized = true\n"


def test_existing_file_accepted_prompt_overwrites(dev_toml: Path, monkeypatch: pytest.MonkeyPatch):
    dev_toml.write_text("customized = true\n")
    monkeypatch.setattr(typer, "confirm", mock.Mock(return_value=True))

    setup_env.write_dev_config(TOKEN, (), force=False)

    assert f'token = "{TOKEN}"' in dev_toml.read_text()


def test_force_overwrites_without_prompting(dev_toml: Path, monkeypatch: pytest.MonkeyPatch):
    dev_toml.write_text("customized = true\n")
    confirm = mock.Mock()
    monkeypatch.setattr(typer, "confirm", confirm)

    setup_env.write_dev_config(TOKEN, (), force=True)

    confirm.assert_not_called()
    assert f'token = "{TOKEN}"' in dev_toml.read_text()


def test_admin_id_sets_admin_tg_ids(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (111, 222), force=True)

    parsed = tomllib.loads(dev_toml.read_text())
    assert parsed["bot"]["admin_tg_ids"] == [111, 222]
    assert load_config(dev_toml.parent).bot.admin_tg_ids == [111, 222]


def test_admin_id_omitted_leaves_key_absent(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)

    parsed = tomllib.loads(dev_toml.read_text())
    assert "admin_tg_ids" not in parsed["bot"]
    assert load_config(dev_toml.parent).bot.admin_tg_ids == []


def test_setup_command_writes_dev_config_when_token_given(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    write_dev_config = mock.Mock()
    monkeypatch.setattr(setup_env, "write_dev_config", write_dev_config)
    monkeypatch.setattr(setup_env, "main_checkout_root", lambda: Path("/"))
    monkeypatch.setattr(setup_env, "copy_local_only_files", lambda main_root, current_root: [])
    monkeypatch.setattr(setup_env.shutil, "which", lambda name: None)

    result = cli.invoke(app, ["setup", "--bot-token", TOKEN, "--admin-id", "111"])

    assert result.exit_code == 0, result.output
    write_dev_config.assert_called_once_with(TOKEN, (111,), False)


def test_setup_command_skips_dev_config_without_token(recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch):
    write_dev_config = mock.Mock()
    monkeypatch.setattr(setup_env, "write_dev_config", write_dev_config)
    monkeypatch.setattr(setup_env, "main_checkout_root", lambda: Path("/"))
    monkeypatch.setattr(setup_env, "copy_local_only_files", lambda main_root, current_root: [])
    monkeypatch.setattr(setup_env.shutil, "which", lambda name: None)

    result = cli.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    write_dev_config.assert_not_called()
