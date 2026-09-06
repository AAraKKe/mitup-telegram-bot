"""Which showcase fills each `<!-- mock:NAME -->` slot of the user guide."""

from __future__ import annotations

from pathlib import Path

from showcase import annotated, group_header, place

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "user-guide"

# The sample meeting the dumps render, renamed to the docs' fictitious cast.
HIKE = [
    ("Board game night", "Weekend hike prep"),
    ("Bring snacks", "Bring water and sturdy boots."),
    ("The usual bar", "Trailhead car park"),
    (">Owner<", ">Ana Marín<"),
    ("Owner ", "Ana Marín "),
    ("Created by: Owner", "Created by: Ana Marín"),
    ("Guest 0", "Diego"),
    ("Guest 1", "Sara"),
    ("Guest 2", "Tomás"),
    ("Guest 3", "Marta"),
]


def fill_all():
    place(
        GUIDE / "main_menu.md",
        "main_menu",
        annotated(
            "main_menu",
            [
                ("➕ New meeting", "New meeting", "left"),
                ("📂 Active · 2", "Your lists", "right"),
                ("⚙️ Settings<", "Account", "left"),
            ],
        ),
    )
    place(
        GUIDE / "main_menu.md",
        "active_list",
        annotated(
            "list_section_active",
            [("Weekend hike prep", "One meeting", "left"), (">Open<", "Open or delete", "right")],
            extra_rows=[[("≪ Main Menu", None)]],
            rename=HIKE,
        ),
    )
    place(
        GUIDE / "create_a_meeting.md",
        "owner_card",
        annotated(
            "owner_card_with_images",
            [
                ("mitup-card__photos--collage", "Photos", "right"),
                ("Start time", "When", "left"),
                ("Change limit", "Guests", "right"),
                ("📨 Share", "Card buttons", "left"),
            ],
            rename=HIKE,
        ),
    )
    place(
        GUIDE / "create_a_meeting.md",
        "when_editor",
        annotated(
            "when_start_editor",
            [
                (">Mon<", "Pick a day", "left"),
                (">September<", "Month and year", "right"),
                ("Current time", "The time", "left"),
            ],
        ),
    )
    place(
        GUIDE / "meeting_settings.md",
        "meeting_settings",
        annotated(
            "meeting_settings", [("🇺🇸 English", "Language", "left"), ("Behavior</strong>", "Opens a screen", "right")]
        ),
    )
    place(
        GUIDE / "meeting_settings.md",
        "meeting_behavior",
        annotated("meeting_behavior", [("Waiting list", "Tap to flip", "right")]),
    )
    place(
        GUIDE / "settings.md",
        "user_settings",
        annotated(
            "user_settings",
            [("Timezone</strong>", "Change chips", "right"), ("Default Options</strong>", "Defaults", "left")],
            rename=[("<br/>UTC<br/>", "<br/>Europe/Madrid<br/>"), ("active 1 minutes", "active 5 minutes")],
        ),
    )
    place(
        GUIDE / "sharing_and_joining.md",
        "shared_card",
        annotated(
            "shared_card_inline",
            [
                ("Created by", "Who made it", "left"),
                ("Not searchable", "Searchable?", "left"),
                ("✅ Join", "RSVP row", "right"),
            ],
            classic=True,
            rename=HIKE,
        ),
    )
    place(
        GUIDE / "meeting_lifecycle.md",
        "reminder",
        annotated(
            "reminder_starting_soon",
            [("Starts in", "Counts down", "left"), ("❌ Leave", "Can't make it", "right")],
            rename=HIKE,
        ),
    )
    place(
        GUIDE / "meeting_lifecycle.md",
        "past_meeting",
        annotated(
            "past_meeting",
            [("This meeting has finished", "Finished", "left"), ("Reactivate meeting", "Bring it back", "right")],
            rename=HIKE,
        ),
    )
    place(
        GUIDE / "inline_mode.md",
        "inline_results",
        annotated(
            "inline_results",
            [("Meetings in this chat", "Chat's meetings", "right"), ("Weekend hike prep", "Your meetings", "left")],
            header=group_header("Hiking crew", 6, "🏔️"),
            panel=True,
            rename=HIKE,
        ),
    )


PAGES = sorted(
    {
        GUIDE / name
        for name in (
            "main_menu.md",
            "create_a_meeting.md",
            "meeting_settings.md",
            "settings.md",
            "sharing_and_joining.md",
            "meeting_lifecycle.md",
            "inline_mode.md",
        )
    }
)
