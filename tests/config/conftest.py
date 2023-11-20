from typing import Generator
from unittest import mock

import pytest

from mitup_bot.config import ConfigMap


@pytest.fixture
def mock_toml_config(
    request: pytest.FixtureRequest,
) -> Generator[tuple[mock.Mock, mock.Mock], None, None]:
    """
    This fixture makes sure that in every test in this module we are not actuallyr eading any resources
    so our tests do not depend on what is or not stored on disk. This ensure the test is self-contained
    and invariant to any changes to configuration files.

    The content of the toml file will be extracted from the parameter inyected by each test that requests
    the fixture.
    """
    with mock.patch("mitup_bot.config.files") as mock_files:
        # Mock the resources files method to avoid attempting to read resources during tests
        with mock.patch("mitup_bot.config.as_file") as mock_as_file:
            # Mock the as_file method as there is not real resource that needs to be read as file
            with mock.patch(
                "mitup_bot.config.open", mock.mock_open(read_data=request.param)
            ):
                # Mock the open method to return the expected toml content when opening the file
                # Once all patches are applied just yield them to the test
                yield (mock_files, mock_as_file)


@pytest.fixture
def mock_env_config(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """
    This fixture recevies a ConfigMap from the tests that request it and update the
    environment variables through monkeypatch with the expected variables representing
    the configuration.
    """
    config_map: ConfigMap = request.param

    for group, config in config_map.items():
        for key, value in config.items():
            key = f"mitupbot__{group}__{key}"
            monkeypatch.setenv(key.upper(), str(value))
