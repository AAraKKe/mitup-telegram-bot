from __future__ import annotations

from collections.abc import Sequence

from mitup_bot import docs_links
from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import MeetingCounts, User
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import (
    AdminMessages,
    ButtonMessages,
    CommonMessages,
    Emojis,
    HelpMessages,
    Languages,
    MainMenuMessages,
    MeetingCreationMessages,
    MeetingLifecycleMessages,
    PrivacyMessages,
    SettingsMessages,
)
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MessageBase
from mitup_bot.utils.rich_message import (
    RichContent,
    RichDocument,
    RichPhoto,
    RichTag,
    horizontal_rule_content,
    keyboard_content,
)
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views import MitupView, RenderContext
from mitup_bot.views.mitup_view import arrange_in_grid
from mitup_bot.views.sections import chip_section, section_header

# Representation from language code to button to be used when generating views
LANGUAGE_BUTTONS = {
    "es_ES": Languages.SPANISH,
    "gl_ES": Languages.GALICIAN,
    "en": Languages.ENGLISH,
    "de_DE": Languages.GERMAN,
    "pt_BR": Languages.PORTUGUESE,
    "it_IT": Languages.ITALIAN,
}

# How many languages a row of the picker holds. Every surface that offers the languages arranges
# them the same way, so the grid reads identically wherever it is shown.
LANGUAGE_GRID_COLUMNS = 3

# Public Telegram handles, identical in every environment
COMMUNITY_GROUP_URL = "https://t.me/mitupgroup"
NEWS_CHANNEL_URL = "https://t.me/meetupnews"


def main_menu_back_rows(lang: str) -> Keyboard:
    """The single "Back to main menu" row a screen falls back to when it navigates nowhere else."""
    return [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]]


def meeting_list_chip(label: ButtonMessages, lang: str, callback_data: CallbackData, count: int | None) -> ButtonConfig:
    """An empty list opens on an alert rather than a screen, so its chip is inert."""
    if count is None:
        return ButtonConfig(text=label.text(lang=lang), callback_data=callback_data)
    text = ButtonMessages.COUNTED_CHIP.text(lang=lang, label=label.text(lang=lang), count=count)
    if count == 0:
        return ButtonConfig(text=text, disabled=True)
    return ButtonConfig(text=text, callback_data=callback_data)


def meetings_section(lang: str, counts: MeetingCounts | None) -> RichContent:
    active, joined, past = (counts.active, counts.joined, counts.past) if counts is not None else (None, None, None)
    rows: Keyboard = [
        [
            meeting_list_chip(
                ButtonMessages.ACTIVE_MEETINGS_CHIP, lang, cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1), active
            ),
            meeting_list_chip(
                ButtonMessages.JOINED_MEETINGS_CHIP, lang, cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1), joined
            ),
        ],
        [meeting_list_chip(ButtonMessages.PAST_MEETINGS_CHIP, lang, cb.PAST_MEETINGS, past)],
    ]
    return section_header(MainMenuMessages.MEETINGS_SECTION, Emojis.CALENDAR, lang).append(keyboard_content(rows))


def account_section(lang: str) -> RichContent:
    rows: Keyboard = [
        [
            ButtonConfig(text=ButtonMessages.SETTINGS.text(lang=lang), callback_data=cb.SETTINGS),
            ButtonConfig(text=ButtonMessages.HELP.text(lang=lang), callback_data=cb.HELP),
        ],
        [ButtonConfig(text=ButtonMessages.COLLABORATE.text(lang=lang), callback_data=cb.COLLABORATE)],
    ]
    return section_header(MainMenuMessages.ACCOUNT_SECTION, Emojis.SETTINGS, lang).append(keyboard_content(rows))


def main_menu_opening(lang: str) -> RichContent:
    return MainMenuMessages.WELCOME.rich(lang=lang).wrap(RichTag.H1).append(MainMenuMessages.TAGLINE.rich(lang=lang))


def main_menu_view(
    ctx: RenderContext, *, message: RichContent | None = None, counts: MeetingCounts | None = None
) -> MitupView:
    """*message* replaces the welcome heading and tagline; the rest of the menu always renders.

    *counts* sizes the three list chips; without it they render unnumbered and all tappable.
    """
    lang = ctx.lang
    new_meeting = ButtonConfig(
        text=ButtonMessages.NEW_MEETING.text(lang=lang), callback_data=cb.CREATE_MEETING, style="primary"
    )
    opening = (message or main_menu_opening(lang)).append(keyboard_content([[new_meeting]]))
    body = RichContent.join(horizontal_rule_content(), [opening, meetings_section(lang, counts), account_section(lang)])
    menu: Keyboard = []
    if ctx.is_admin:
        menu.append([ButtonConfig(text=AdminMessages.BUTTON_ADMIN.text(lang=lang), callback_data=cb.ADMIN_MENU)])
    return MitupView(body, menu=menu)


def admin_menu_view(ctx: RenderContext) -> MitupView:
    lang = ctx.lang
    return MitupView(
        AdminMessages.MENU_DESCRIPTION.rich(lang=lang),
        [
            [
                ButtonConfig(text=AdminMessages.BUTTON_BROADCAST.text(lang=lang), callback_data=cb.BROADCAST),
                ButtonConfig(
                    text=AdminMessages.BUTTON_SUPPORTER_GRANTS.text(lang=lang),
                    callback_data=cb.SUPPORTER_GRANT,
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=lang),
                    callback_data=cb.MAIN_MENU,
                )
            ],
        ],
    )


def language_grid_content(lang: str, current: str, callback_data: CallbackData) -> RichContent:
    """The callback carries the language's position in `SUPPORTED_LANGUAGES`, since a language
    code does not fit the callback's numeric id."""
    buttons = [
        ButtonConfig(
            text=LANGUAGE_BUTTONS[code].text(lang=lang),
            callback_data=callback_data.with_id(index),
            style="primary" if code == current else None,
        )
        for index, code in enumerate(SUPPORTED_LANGUAGES)
    ]
    return keyboard_content(arrange_in_grid(buttons, min(len(SUPPORTED_LANGUAGES), LANGUAGE_GRID_COLUMNS)))


def settings_section(title: ButtonMessages, lang: str, chip: ButtonConfig, body: RichContent) -> RichContent:
    header = title.rich(lang=lang).wrap(RichTag.BOLD)
    return render_rich(t"{header} {chip}").append("\n").append(body)


def language_settings_section(lang: str) -> RichContent:
    header = ButtonMessages.LANGUAGE.rich(lang=lang).wrap(RichTag.BOLD)
    return header.append(language_grid_content(lang, current=lang, callback_data=cb.SET_LANGUAGE))


def timezone_settings_section(lang: str, timezone: str) -> RichContent:
    """The city of the stored zone id names the timezone: "Europe/Madrid" reads as "Madrid"."""
    city = timezone.rpartition("/")[2].replace("_", " ")
    change = ButtonConfig(text=ButtonMessages.CHANGE.text(lang=lang), callback_data=cb.EDIT_TIEMZONE)
    return settings_section(ButtonMessages.TIMEZONE, lang, change, RichContent(city))


def notifications_settings_section(lang: str, enabled: bool, lead_minutes: int) -> RichContent:
    change = ButtonConfig(text=ButtonMessages.CHANGE.text(lang=lang), callback_data=cb.SET_NOTIFICATION_TIME)
    lead = SettingsMessages.NOTIFICATION_LEAD.rich(lang=lang, minutes=lead_minutes, button_change=change)
    return settings_section(
        ButtonMessages.NOTIFICATIONS, lang, toggle_chip(cb.TOGGLE_NOTIFICATIONS, enabled, lang), lead
    )


def timeout_settings_section(lang: str, timeout_minutes: int) -> RichContent:
    change = ButtonConfig(text=ButtonMessages.CHANGE.text(lang=lang), callback_data=cb.EDIT_TIMEOUT)
    value = SettingsMessages.TIMEOUT_VALUE.rich(lang=lang, minutes=timeout_minutes)
    return settings_section(ButtonMessages.TIMEOUT, lang, change, value)


def settings_view(ctx: RenderContext, user: User) -> MitupView:
    lang = ctx.lang
    settings = user.settings
    heading = ButtonMessages.SETTINGS.rich(lang=lang).wrap(RichTag.H2)
    account = RichContent.join(
        "\n\n",
        [
            timezone_settings_section(lang, settings.timezone),
            notifications_settings_section(lang, settings.notification, settings.notification_time),
            timeout_settings_section(lang, settings.timeout),
        ],
    )
    screens = RichContent.join(
        "\n\n",
        [
            settings_section(
                ButtonMessages.DEFAULT_OPTIONS,
                lang,
                ButtonConfig(text=ButtonMessages.OPEN.text(lang=lang), callback_data=cb.EDIT_DEFAULT_OPTIONS),
                SettingsMessages.DEFAULT_OPTIONS_LINE.rich(lang=lang),
            ),
            settings_section(
                ButtonMessages.PRIVACY,
                lang,
                ButtonConfig(text=ButtonMessages.OPEN.text(lang=lang), callback_data=cb.EDIT_PRIVACY),
                SettingsMessages.PRIVACY_LINE.rich(lang=lang),
            ),
        ],
    )
    body = RichContent.join(horizontal_rule_content(), [heading, language_settings_section(lang), account, screens])
    return MitupView(body, main_menu_back_rows(lang))


def help_view(ctx: RenderContext) -> MitupView:
    lang = ctx.lang
    heading = ButtonMessages.HELP.rich(lang=lang).wrap(RichTag.H2).append(HelpMessages.INTRO.rich(lang=lang))
    guide = ButtonConfig(text=ButtonMessages.USER_GUIDE.text(lang=lang), url=docs_links.user_guide_url())
    group = ButtonConfig(text=ButtonMessages.COMMUNITY_GROUP.text(lang=lang), url=COMMUNITY_GROUP_URL)
    channel = ButtonConfig(text=ButtonMessages.NEWS_CHANNEL.text(lang=lang), url=NEWS_CHANNEL_URL)
    blocks = [
        heading,
        HelpMessages.USER_GUIDE.rich(lang=lang, button_guide=guide),
        HelpMessages.COMMUNITY_GROUP.rich(lang=lang, button_group=group),
        HelpMessages.NEWS_CHANNEL.rich(lang=lang, button_channel=channel),
        HelpMessages.EMAIL.rich(lang=lang),
    ]
    return MitupView(RichContent.join(horizontal_rule_content(), blocks)).with_back_button(
        ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU
    )


def privacy_view(ctx: RenderContext, *, export: RichDocument | None = None) -> MitupView:
    lang = ctx.lang
    heading = ButtonMessages.PRIVACY.rich(lang=lang).wrap(RichTag.H2).append(PrivacyMessages.INTRO.rich(lang=lang))
    policy = chip_section(
        PrivacyMessages.POLICY_TITLE,
        Emojis.SHIELD,
        lang,
        ButtonConfig(text=ButtonMessages.READ.text(lang=lang), url=docs_links.privacy_url()),
        PrivacyMessages.POLICY.rich(lang=lang),
    )
    data = chip_section(
        PrivacyMessages.EXPORT_TITLE,
        Emojis.PACKAGE,
        lang,
        ButtonConfig(text=ButtonMessages.EXPORT.text(lang=lang), callback_data=cb.EXPORT_USER_DATA),
        PrivacyMessages.EXPORT.rich(lang=lang),
    )
    if export is not None:
        data = data.append("\n").append(PrivacyMessages.EXPORT_ATTACHED.rich(lang=lang))
    deletion = chip_section(
        PrivacyMessages.DELETE_TITLE,
        Emojis.DELETE,
        lang,
        ButtonConfig(text=ButtonMessages.DELETE.text(lang=lang), callback_data=cb.DELETE_USER_DATA, style="danger"),
        PrivacyMessages.DELETE.rich(lang=lang),
    )
    body = RichContent.join(horizontal_rule_content(), [heading, policy, data, deletion])
    return MitupView(body, document=export).with_back_button(ButtonMessages.SETTINGS, lang, cb.SETTINGS)


def create_meeting_view(
    ctx: RenderContext, *, message: RichContent | None = None, datetime_link: RichContent | None = None
) -> MitupView:
    lang = ctx.lang
    if message is None:
        kwargs: dict[str, RichContent] = {}
        if datetime_link is not None:
            kwargs["datetime_link"] = datetime_link
        message = MeetingCreationMessages.PROMPT.rich(lang=lang, **kwargs)
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.text(lang=lang), callback_data=cb.CANCEL_CREATE_MEETING),
            ],
        ],
    )


def request_information_with_cancel_view(
    ctx: RenderContext, *, message: RichContent, callback_data: cb.CallbackData
) -> MitupView:
    """
    Use this wen we want to ask the user for information and give them the option to cancel the action.

    The callback_data represents what the action taken when the user clicks on Cancel.
    """
    return MitupView(
        message,
        [
            [
                ButtonConfig(text=ButtonMessages.CANCEL.text(lang=ctx.lang), callback_data=callback_data),
            ],
        ],
    )


def change_settings_element_view(
    ctx: RenderContext, *, message: RichContent, callback_data=cb.CANCEL_SETTINGS
) -> MitupView:
    """
    This view is used when in order to change a setting the user is asked for a message and we want to give
    them the option to Cancel the action and go back to settings.
    """
    return request_information_with_cancel_view(ctx, message=message, callback_data=callback_data)


def toggle_chip(callback_data: CallbackData, option: bool, lang: str) -> ButtonConfig:
    """A boolean setting as an inline chip: the label and styling show the CURRENT state (green
    Enabled / red Disabled) and a tap flips it. The setting's name and explanation live in the
    content beside the chip, so the chip itself carries only the state."""
    label = ButtonMessages.ENABLED if option else ButtonMessages.DISABLED
    return ButtonConfig(
        text=label.text(lang=lang),
        callback_data=callback_data,
        style="success" if option else "danger",
    )


def broadcast_recipient_keyboard(lang: str) -> Keyboard:
    """The single-button row every recipient gets, in that recipient's own language.

    Previews carry it too so the operator sees exactly what will be sent. Plain "Main Menu" label
    (no « decoration) since this is navigation from a standalone message, not a back button.
    """
    return [[ButtonConfig(text=ButtonMessages.MAIN_MENU.text(lang=lang), callback_data=cb.SEND_MAIN_MENU)]]


def broadcast_recipient_view(body: str, lang: str) -> MitupView:
    """The exact view every broadcast recipient receives: the rich body its operator wrote, carried
    unchanged, plus `broadcast_recipient_keyboard` in the recipient's own language.

    Single source of truth so the operator preview and the sender's delivery build an identical
    view: "preview equals delivery" is a guarantee, not two constructions kept in sync by hand.
    """
    return MitupView(RichContent.from_markup(body), broadcast_recipient_keyboard(lang))


def confirmation_view(
    ctx: RenderContext,
    *,
    message: RichContent,
    confirm_callback_data: CallbackData,
    decline_callback_data: CallbackData,
    confirm_label: ButtonMessages = ButtonMessages.CONFIRM,
    decline_label: ButtonMessages = ButtonMessages.DECLINE,
    photos: Sequence[RichPhoto] = (),
) -> MitupView:
    """Accept/decline dialog. The labels default to the generic pair, and a caller overrides them
    where a bare "Confirm" would under-describe what is being agreed to: a prompt whose whole
    purpose is to make someone stop and read needs a button that names the act. *photos* are the
    ones the message shows, when the prompt draws what is about to go."""
    lang = ctx.lang
    return MitupView(
        message,
        [
            [
                ButtonConfig(
                    text=confirm_label.text(lang=lang),
                    callback_data=confirm_callback_data,
                    style="success",
                ),
                ButtonConfig(
                    text=decline_label.text(lang=lang),
                    callback_data=decline_callback_data,
                    style="danger",
                ),
            ],
        ],
        photos=photos,
    )


def conversation_interrupted_view(
    ctx: RenderContext, *, notice: MessageBase, cancel_callback: CallbackData
) -> MitupView:
    """The nudge answering a message the active conversation did not expect: the notice with its
    Cancel chip inline, and no menu."""
    cancel = ButtonConfig(text=ButtonMessages.CANCEL.text(lang=ctx.lang), callback_data=cancel_callback)
    return MitupView(notice.rich(lang=ctx.lang, button_cancel=cancel))


def deleted_meeting_view(ctx: RenderContext, *, back_rows: Keyboard | None = None) -> MitupView:
    """
    View shown to a user who acts on a meeting that no longer exists.

    If `back_rows` is provided it is used as the back-navigation row(s). Otherwise a single
    "Back to main menu" row is rendered.
    """
    lang = ctx.lang
    return MitupView(
        message=CommonMessages.DELETED_MEETING_ALERT.rich(lang=lang),
        menu=back_rows or main_menu_back_rows(lang),
    )


def reactivation_prompt_view(ctx: RenderContext, *, meeting_id: int, back_rows: Keyboard | None = None) -> MitupView:
    """
    View shown to the owner when they try to access a meeting that is no longer active.

    If `back_rows` is provided it is used as the back-navigation row(s) below the action buttons.
    Otherwise a single "Back to main menu" row is rendered.
    """
    lang = ctx.lang
    resolved_back_rows: Keyboard = back_rows or main_menu_back_rows(lang)
    return MitupView(
        message=MeetingLifecycleMessages.PAST_DESCRIPTION.rich(lang=lang),
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.text(lang=lang),
                    callback_data=cb.REACTIVATE_MEETING.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.text(lang=lang),
                    callback_data=cb.DELETE_MEETING.with_id(meeting_id),
                ),
            ],
            *resolved_back_rows,
        ],
    )
