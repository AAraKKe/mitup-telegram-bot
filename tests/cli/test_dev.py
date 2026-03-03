from string import Template
from unittest import mock

from click.testing import CliRunner

from mitup_bot.cli.commands.dev import DEV_CONFIG, cli


def test_cli_writes_config_with_token():
    token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    m = mock.mock_open()

    with (
        mock.patch("builtins.open", m),
        mock.patch("mitup_bot.cli.commands.dev.success") as mock_success,
    ):
        runner = CliRunner()
        result = runner.invoke(cli, [token])

    assert result.exit_code == 0, result.output

    written = "".join(call.args[0] for call in m().write.call_args_list)
    expected = Template(DEV_CONFIG).safe_substitute(telegram_token=token)[1:]
    assert written == expected

    # Token substituted correctly
    assert f'token = "{token}"' in written
    assert "${telegram_token}" not in written

    # Contains all TOML sections
    for section in ("[bot]", "[db]", "[app]", "[google_api]", "[metrics]"):
        assert section in written

    # Success message printed with path reference
    mock_success.assert_called_once()
    assert "dev.toml" in mock_success.call_args[0][0]

    # open() called with a path ending in "environments/dev.toml" in write mode
    dev_toml_open_call = next(
        (call for call in m.call_args_list if call.args and str(call.args[0]).endswith("environments/dev.toml")),
        None,
    )
    assert dev_toml_open_call is not None
    assert dev_toml_open_call.args[1] == "w"
