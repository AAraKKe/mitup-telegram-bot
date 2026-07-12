"""Process-wide holder for the optional hosts-only Telegram group config, injected at startup.

The hosts-only group is optional: a deployment without a ``hosts_group_chat_id`` runs with the
feature fully disabled (every group operation becomes a no-op). The live settings are injected once
at startup via :func:`configure` in both the bot and events processes, and read back through
:func:`chat_id` / :func:`invite_url` / :func:`is_configured` from the join-request handler, the
Patreon web layer, and the daily supporter-check job, none of which hold ``BotConfig`` directly.
"""

from typing import ClassVar

from mitup_bot.config import BotConfig


class HostsGroupState:
    """Holds the hosts-only group settings for the process, or ``None`` when the feature is off."""

    chat_id: ClassVar[int | None] = None
    invite_url: ClassVar[str | None] = None


def configure(config: BotConfig):
    """Adopt the hosts-only group settings. Called once per process at startup."""
    HostsGroupState.chat_id = config.hosts_group_chat_id
    HostsGroupState.invite_url = config.hosts_group_invite_url


def is_configured() -> bool:
    """Whether the hosts-only group feature is enabled in this deployment."""
    return HostsGroupState.chat_id is not None


def chat_id() -> int | None:
    """The hosts-only group chat id, or ``None`` when the feature is disabled."""
    return HostsGroupState.chat_id


def invite_url() -> str | None:
    """The hosts-only group invite URL, or ``None`` when the feature is disabled."""
    return HostsGroupState.invite_url
