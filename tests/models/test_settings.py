import logging

from pytest import LogCaptureFixture

from mitup_bot.models.settings import Settings


def test_valid_timezone(settings: Settings):
    assert settings.tz.key == "Europe/Madrid"


def test_invalid_timezone(settings: Settings, caplog: LogCaptureFixture):
    settings.timezone = "Invalid/Timezone"
    with caplog.at_level(logging.WARNING):
        assert settings.tz.key == "UTC"
        assert "Invalid timezone" in caplog.records[0].message
