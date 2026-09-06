"""Write every user-facing screen as text, rich-message html and closing rows to screens/<name>.txt.

The dumps are the ground truth the docs mockups are generated from; `showcase.py` reads them.
"""

from __future__ import annotations

import datetime as dt
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mitup_bot.handlers.meeting.edit.when import screens as when_screens  # noqa: E402
from mitup_bot.handlers.meeting.show_past_meeting import past_meeting_view  # noqa: E402
from mitup_bot.models import MeetingCounts  # noqa: E402
from mitup_bot.utils import callbacks as cb  # noqa: E402
from mitup_bot.utils.messages import ButtonMessages, InlineQueryMessages  # noqa: E402
from mitup_bot.utils.rich_message import datetime_link_content  # noqa: E402
from mitup_bot.views import (  # noqa: E402
    RenderContext,
    factory,
    meeting_settings,  # noqa: E402
)
from mitup_bot.views import meeting as meeting_views  # noqa: E402
from mitup_bot.views.meeting import images_card, list_card, notifications, settings_card  # noqa: E402
from mitup_bot.views.mitup_view import MitupView  # noqa: E402
from tests.helpers import create_joined_link, create_settings, create_user  # noqa: E402
from tests.telegram.views.meeting.helpers import (  # noqa: E402
    in_progress_meeting,
    owned_meeting,
    populated_meeting,
    with_images,
)

OUT = Path(__file__).parent / "screens"


def dump(name: str, build: Callable[[], MitupView]):
    try:
        view = build()
    except Exception:
        print("FAIL", name)
        traceback.print_exc(limit=2)
        return
    lines = [
        f"### {name}",
        "",
        "--- TEXT ---",
        view.message.text,
        "",
        "--- HTML ---",
        view.message.html,
        "",
        "--- MENU ---",
    ]
    for row in view.menu:
        lines.append(
            " | ".join(
                f"[{button.text}]"
                + (f"(style={button.style})" if button.style else "")
                + ("(disabled)" if button.disabled else "")
                for button in row
            )
        )
    if view.photos:
        lines.append(f"--- PHOTOS --- {[photo.media_id for photo in view.photos]}")
    (OUT / f"{name}.txt").write_text("\n".join(lines) + "\n")
    print("ok  ", name)


def dump_inline_results(meetings):
    """The panel Telegram draws for an empty `@mitupbot` query: the top button, the chat's own entry
    and one row per active meeting, as the inline handler answers it."""
    lines = [
        "### inline_results",
        "",
        f"TOP|{InlineQueryMessages.CREATE_MEETING_BUTTON.text(lang='en')}",
        f"ROW|🔍|{InlineQueryMessages.CHAT_MEETINGS_TITLE.text(lang='en')}|{InlineQueryMessages.CHAT_MEETINGS_DESCRIPTION.text(lang='en')}",
    ]
    for meeting in meetings:
        result = meeting_views.inline_view(meeting)
        lines.append(f"ROW|{result.title[0]}|{result.title}|{result.inline_description.replace(chr(10), '<br/>')}")
    (OUT / "inline_results.txt").write_text("\n".join(lines) + "\n")
    print("ok   inline_results")


def main():
    OUT.mkdir(exist_ok=True)
    ctx = RenderContext(lang="en")
    meeting = populated_meeting(guests=3, owner_joins=True, waiting=1, public=True, invitation=True)
    with_photos = with_images(populated_meeting(guests=2, owner_joins=True), 3)
    empty = owned_meeting()
    owner = meeting.owner
    guest = create_user(
        id=50, tg_user_id=50, first_name="Lucía", settings=create_settings(id=50, timezone="Europe/Madrid")
    )
    link = create_joined_link(guest, meeting, id=99)
    past = populated_meeting(guests=2, owner_joins=True)
    past.active = False
    second = owned_meeting(guests=5, owner_joins=True)
    second.set_title("Book club")
    second.datetime = dt.datetime(2026, 9, 18, 18, 0, tzinfo=dt.UTC)
    assert meeting.datetime is not None

    dump("main_menu", lambda: factory.main_menu_view(ctx, counts=MeetingCounts(active=2, joined=1, past=3)))
    dump("help", lambda: factory.help_view(ctx))
    dump("privacy", lambda: factory.privacy_view(ctx))
    dump("create_meeting_prompt", lambda: factory.create_meeting_view(ctx, datetime_link=datetime_link_content()))
    dump("user_settings", lambda: factory.settings_view(ctx, owner))
    dump("default_meeting_options", lambda: meeting_settings.default_meeting_settings_view(owner.settings))
    dump("default_behavior", lambda: meeting_settings.default_behavior_view(owner.settings))
    dump("default_time_format", lambda: meeting_settings.default_time_format_view(owner.settings))
    dump("meeting_settings", lambda: settings_card.settings_view(meeting))
    dump("meeting_behavior", lambda: settings_card.behavior_view(meeting))
    dump("meeting_time_format", lambda: settings_card.time_format_view(meeting))
    dump("owner_card", lambda: meeting_views.owner_view(meeting))
    dump("owner_card_with_images", lambda: meeting_views.owner_view(with_photos))
    dump("owner_card_empty", lambda: meeting_views.owner_view(empty))
    dump("owner_card_in_progress", lambda: meeting_views.owner_view(in_progress_meeting(locked=True)))
    dump("shared_card_bot_chat", lambda: meeting_views.external_view(meeting))
    dump("shared_card_inline", lambda: meeting_views.inline_view(meeting))
    dump("shared_card_inline_searchable", lambda: meeting_views.inline_view(meeting, chat_instance="123"))
    dump("shared_card_with_images", lambda: meeting_views.external_view(with_photos))
    dump("left_confirmation", lambda: meeting_views.left_view(meeting, "en"))
    dump("images_screen", lambda: images_card.images_view(with_photos))
    dump("images_locked", lambda: images_card.images_locked_view(with_photos))
    dump("images_remove_prompt", lambda: images_card.remove_image_prompt(ctx, with_photos, 1))
    dump(
        "list_section_active",
        lambda: MitupView(
            list_card.list_heading(ButtonMessages.ACTIVE_MEETINGS, "en").append(
                list_card.meeting_list_section(
                    meeting, cb.SHOW_MEETING.with_id(1), "en", delete_callback=cb.DELETE_MEETING.with_id(1)
                )
            )
        ),
    )
    dump(
        "reminder_starting_soon",
        lambda: notifications.starting_soon_view(link, now=meeting.datetime - dt.timedelta(minutes=15)),
    )
    dump("reminder_started", lambda: notifications.started_view(link, now=meeting.datetime + dt.timedelta(minutes=5)))
    dump("when_start_editor", lambda: when_screens.start_datetime_card(meeting, "en", month=dt.date(2026, 9, 1)))
    dump("when_end_editor", lambda: when_screens.end_datetime_card(meeting, "en", month=dt.date(2026, 9, 1)))
    dump("past_meeting", lambda: past_meeting_view(past, past.owner, 1))
    dump_inline_results([meeting, second])


if __name__ == "__main__":
    main()
