import tomllib
from pathlib import Path
from unittest import mock

import pytest
import typer
from command_recording import CommandRecorder
from mb.main import app
from pydantic import BaseModel
from typer.testing import CliRunner

from mb import console, setup_env
from mitup_bot.config import Config, Env, PatreonConfig, TomlConfigProvider

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


def test_dev_toml_path_matches_config_resolution():
    """The writer must target the exact directory the config loader reads, or `mb setup` writes a
    dev.toml nothing loads (the workspace-split regression this guards against)."""
    from importlib.resources import files

    from mitup_bot import environments

    expected = Path(str(files(environments))) / f"{Env.DEV.value}.toml"
    assert setup_env.dev_toml_path() == expected


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


def test_every_required_field_carries_a_sample():
    for section, model in setup_env.required_sections().items():
        for name, field in model.model_fields.items():
            if field.is_required():
                assert setup_env.field_sample(field) is not None, f"[{section}] {name} has no Sample"


def test_patreon_samples_validate():
    """The section is optional today; the moment the model makes it required, generation and
    refresh must already have working sample values for it."""
    entries = setup_env.section_entries("patreon", PatreonConfig)

    PatreonConfig.model_validate(tomllib.loads("\n".join(entries)))


def test_required_field_without_sample_raises():
    class Incomplete(BaseModel):
        needed: str

    with pytest.raises(RuntimeError, match=r"\[incomplete\] needed"):
        setup_env.section_entries("incomplete", Incomplete)


def test_token_is_substituted(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)

    assert f'token = "{TOKEN}"' in dev_toml.read_text()


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


def drop_lines(dev_toml: Path, prefix: str):
    kept = [line for line in dev_toml.read_text().splitlines() if not line.startswith(prefix)]
    dev_toml.write_text("\n".join(kept) + "\n")


def test_refresh_adds_missing_required_key_inside_its_section(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    drop_lines(dev_toml, "username = ")

    setup_env.refresh_dev_config()

    assert tomllib.loads(dev_toml.read_text())["db"]["username"] == "mitupbot"


def test_refresh_preserves_existing_values(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    dev_toml.write_text(dev_toml.read_text().replace('password = "12345pass"', 'password = "custom-secret"'))
    drop_lines(dev_toml, "url = ")

    setup_env.refresh_dev_config()

    parsed = tomllib.loads(dev_toml.read_text())
    assert parsed["db"]["password"] == "custom-secret"
    assert parsed["db"]["url"] == "postgres"
    assert parsed["bot"]["token"] == TOKEN


def test_refresh_appends_missing_section(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    dev_toml.write_text(dev_toml.read_text().split("[metrics]")[0])

    setup_env.refresh_dev_config()

    assert tomllib.loads(dev_toml.read_text())["metrics"] == {"namespace": "MitupBot", "environment": "rich"}


def test_refresh_does_not_add_defaulted_keys_to_existing_sections(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    drop_lines(dev_toml, "engine_echo = ")

    setup_env.refresh_dev_config()

    assert "engine_echo" not in tomllib.loads(dev_toml.read_text())["db"]


def test_refresh_leaves_complete_file_untouched(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    before = dev_toml.read_text()

    setup_env.refresh_dev_config()

    assert dev_toml.read_text() == before


def test_refreshed_config_validates(dev_toml: Path):
    setup_env.write_dev_config(TOKEN, (), force=True)
    drop_lines(dev_toml, "run_mode = ")
    drop_lines(dev_toml, "namespace = ")

    setup_env.refresh_dev_config()

    assert load_config(dev_toml.parent).app.run_mode.value == "polling"


def test_refresh_rejects_invalid_toml(dev_toml: Path):
    dev_toml.write_text("this is [not toml\n")

    with pytest.raises(typer.Exit):
        setup_env.refresh_dev_config()

    assert dev_toml.read_text() == "this is [not toml\n"


def command_environment(monkeypatch: pytest.MonkeyPatch, dev_toml: Path) -> dict[str, mock.Mock]:
    mocks = {"write_dev_config": mock.Mock(), "refresh_dev_config": mock.Mock()}
    for name, function_mock in mocks.items():
        monkeypatch.setattr(setup_env, name, function_mock)
    monkeypatch.setattr(setup_env, "dev_toml_path", lambda: dev_toml)
    monkeypatch.setattr(setup_env, "main_checkout_root", lambda: Path("/"))
    monkeypatch.setattr(setup_env, "copy_local_only_files", lambda main_root, current_root: [])
    monkeypatch.setattr(setup_env.shutil, "which", lambda name: None)
    return mocks


def test_setup_command_writes_dev_config_when_token_given(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mocks = command_environment(monkeypatch, tmp_path / "dev.toml")

    result = cli.invoke(app, ["setup", "--bot-token", TOKEN, "--admin-id", "111"])

    assert result.exit_code == 0, result.output
    mocks["write_dev_config"].assert_called_once_with(TOKEN, (111,), False)
    mocks["refresh_dev_config"].assert_not_called()


def test_setup_command_refreshes_existing_dev_config_without_token(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    existing = tmp_path / "dev.toml"
    existing.write_text("[bot]\n")
    mocks = command_environment(monkeypatch, existing)

    result = cli.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    mocks["write_dev_config"].assert_not_called()
    mocks["refresh_dev_config"].assert_called_once_with()


def test_setup_command_skips_dev_config_without_token_or_file(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mocks = command_environment(monkeypatch, tmp_path / "dev.toml")

    result = cli.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    mocks["write_dev_config"].assert_not_called()
    mocks["refresh_dev_config"].assert_not_called()


def test_setup_command_prompts_for_token_on_interactive_first_run(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mocks = command_environment(monkeypatch, tmp_path / "dev.toml")
    monkeypatch.setattr(setup_env, "stdin_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "prompt", mock.Mock(return_value=f"  {TOKEN} "))

    result = cli.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    mocks["write_dev_config"].assert_called_once_with(TOKEN, (), force=True)


def test_setup_command_prompt_left_empty_skips_dev_config(
    recorder: CommandRecorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mocks = command_environment(monkeypatch, tmp_path / "dev.toml")
    monkeypatch.setattr(setup_env, "stdin_is_interactive", lambda: True)
    monkeypatch.setattr(typer, "prompt", mock.Mock(return_value=""))

    result = cli.invoke(app, ["setup"])

    assert result.exit_code == 0, result.output
    mocks["write_dev_config"].assert_not_called()
    assert "--bot-token" in result.output
