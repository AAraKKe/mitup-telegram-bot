from collections.abc import Iterator

import pytest

from mitup_bot.exceptions import PatreonNotConfigured
from mitup_bot.patreon import runtime
from mitup_bot.patreon.runtime import PatreonRuntime
from tests.helpers import create_patreon_config


@pytest.fixture(autouse=True)
def reset_runtime() -> Iterator[None]:
    saved = PatreonRuntime.config
    PatreonRuntime.config = None
    try:
        yield
    finally:
        PatreonRuntime.config = saved


def test_unconfigured_by_default():
    assert runtime.is_configured() is False


def test_current_config_raises_when_unconfigured():
    with pytest.raises(PatreonNotConfigured):
        runtime.current_config()


def test_configure_makes_config_available():
    config = create_patreon_config()
    runtime.configure(config)

    assert runtime.is_configured() is True
    assert runtime.current_config() is config
