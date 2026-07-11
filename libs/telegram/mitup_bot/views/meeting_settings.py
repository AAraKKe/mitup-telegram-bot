from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.utils import ButtonMessages, SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views.factory import options_button

if TYPE_CHECKING:
    from mitup_bot.models import Settings


def default_meeting_settings_view(settings: Settings) -> MitupView:
    keyboard = [
        [
            options_button(
                cb.SET_DEFAULT_WAITING_LIST,
                ButtonMessages.WAITING_LIST.get(lang=settings.language),
                settings.default_waiting_list,
            ),
            options_button(
                cb.SET_DEFAULT_PUBLIC,
                ButtonMessages.PUBLIC.get(lang=settings.language),
                settings.default_public,
            ),
        ],
        [
            options_button(
                cb.SET_DEFAULT_INVITATIONS,
                ButtonMessages.OPEN_INVITATION.get(lang=settings.language),
                settings.default_allow_invitation,
            ),
            options_button(
                cb.SET_DEFAULT_INCOGNITO,
                ButtonMessages.INCOGNITO.get(lang=settings.language),
                settings.default_incognito,
            ),
        ],
        [
            options_button(
                cb.SET_DEFAULT_LOCK_ON_START,
                ButtonMessages.LOCK_ON_START.get(lang=settings.language),
                settings.default_lock_on_start,
            ),
        ],
    ]

    return MitupView(
        SettingsMessages.DEFAULT_OPTIONS_DESCRIPTION.get(lang=settings.language),
        keyboard=keyboard,
    ).with_back_button(ButtonMessages.SETTINGS, settings.language, cb.SETTINGS)
