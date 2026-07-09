import tomllib
from pathlib import Path
from unittest import mock

from click.testing import CliRunner, Result

from mitup_bot.cli.commands.dev import cli
from mitup_bot.config import Config, Env, TomlConfigProvider

TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def invoke_dev(tmp_path: Path, args: list[str], input_text: str | None = None) -> tuple[Result, Path]:
    target = tmp_path / "dev.toml"
    with mock.patch("mitup_bot.cli.commands.dev.dev_toml_path", return_value=target):
        result = CliRunner().invoke(cli, args, input=input_text)
    return result, target


def load_config(tmp_path: Path) -> Config:
    """Load the generated file through the real provider chain — the drift gate.

    `TomlConfigProvider` resolves `environments/dev.toml` via `importlib.resources.files`,
    so pointing `files` at the temp directory makes it read the file the command just wrote.
    """
    with mock.patch("mitup_bot.config.files", return_value=tmp_path):
        return Config.from_providers(TomlConfigProvider(Env.DEV))


def test_generated_config_validates(tmp_path: Path):
    result, _ = invoke_dev(tmp_path, [TOKEN])

    assert result.exit_code == 0, result.output
    config = load_config(tmp_path)
    assert config.bot.token.get_secret_value() == TOKEN
    assert config.db.engine_echo is True


def test_generated_sections_cover_required_config_sections(tmp_path: Path):
    result, target = invoke_dev(tmp_path, [TOKEN])
    assert result.exit_code == 0, result.output

    generated_sections = set(tomllib.loads(target.read_text()))
    required_sections = {name for name, field in Config.model_fields.items() if field.is_required()}
    assert required_sections <= generated_sections


def test_token_is_substituted(tmp_path: Path):
    result, target = invoke_dev(tmp_path, [TOKEN])
    assert result.exit_code == 0, result.output

    written = target.read_text()
    assert f'token = "{TOKEN}"' in written
    assert "${" not in written


def test_existing_file_declined_prompt_leaves_it_untouched(tmp_path: Path):
    target = tmp_path / "dev.toml"
    target.write_text("customized = true\n")

    result, _ = invoke_dev(tmp_path, [TOKEN], input_text="n\n")

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert target.read_text() == "customized = true\n"


def test_existing_file_accepted_prompt_overwrites(tmp_path: Path):
    target = tmp_path / "dev.toml"
    target.write_text("customized = true\n")

    result, _ = invoke_dev(tmp_path, [TOKEN], input_text="y\n")

    assert result.exit_code == 0, result.output
    assert f'token = "{TOKEN}"' in target.read_text()


def test_force_overwrites_without_prompting(tmp_path: Path):
    target = tmp_path / "dev.toml"
    target.write_text("customized = true\n")

    result, _ = invoke_dev(tmp_path, [TOKEN, "--force"])

    assert result.exit_code == 0, result.output
    assert "already exists" not in result.output
    assert f'token = "{TOKEN}"' in target.read_text()


def test_admin_id_sets_admin_tg_ids(tmp_path: Path):
    result, target = invoke_dev(tmp_path, [TOKEN, "--admin-id", "111", "--admin-id", "222"])
    assert result.exit_code == 0, result.output

    parsed = tomllib.loads(target.read_text())
    assert parsed["bot"]["admin_tg_ids"] == [111, 222]
    assert load_config(tmp_path).bot.admin_tg_ids == [111, 222]


def test_admin_id_omitted_leaves_key_absent(tmp_path: Path):
    result, target = invoke_dev(tmp_path, [TOKEN])
    assert result.exit_code == 0, result.output

    parsed = tomllib.loads(target.read_text())
    assert "admin_tg_ids" not in parsed["bot"]
    assert load_config(tmp_path).bot.admin_tg_ids == []
