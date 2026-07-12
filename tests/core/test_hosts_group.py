from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from mitup_bot import hosts_group
from mitup_bot.config import BotConfig
from mitup_bot.hosts_group import HostsGroupState

HOSTS_GROUP_CHAT_ID = -1001234567890
INVITE_URL = "https://t.me/+abcdefghijklmnop"


@pytest.fixture(autouse=True)
def reset_hosts_group() -> Iterator[None]:
    saved_chat_id = HostsGroupState.chat_id
    saved_invite_url = HostsGroupState.invite_url
    HostsGroupState.chat_id = None
    HostsGroupState.invite_url = None
    try:
        yield
    finally:
        HostsGroupState.chat_id = saved_chat_id
        HostsGroupState.invite_url = saved_invite_url


def make_bot_config(chat_id: int | None, invite_url: str | None) -> BotConfig:
    return BotConfig(
        token=SecretStr("fake-bot-token"),
        hosts_group_chat_id=chat_id,
        hosts_group_invite_url=invite_url,
    )


def test_disabled_by_default():
    assert hosts_group.is_configured() is False
    assert hosts_group.chat_id() is None
    assert hosts_group.invite_url() is None


def test_configure_adopts_settings():
    hosts_group.configure(make_bot_config(HOSTS_GROUP_CHAT_ID, INVITE_URL))

    assert hosts_group.is_configured() is True
    assert hosts_group.chat_id() == HOSTS_GROUP_CHAT_ID
    assert hosts_group.invite_url() == INVITE_URL


def test_configure_without_chat_id_stays_disabled():
    hosts_group.configure(make_bot_config(None, None))

    assert hosts_group.is_configured() is False
    assert hosts_group.chat_id() is None
