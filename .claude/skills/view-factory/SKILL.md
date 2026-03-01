---
name: view-factory
description: Factory functions catalogue for mitup_bot views. Auto-load when building a new screen to check if a factory function already covers the pattern before constructing a view manually.
user-invocable: false
---

# View Factory

`views/factory.py` contains stateless functions for common screen types. **Always check this catalogue before building a view manually.**

## Available factory functions

| Function | Purpose |
|----------|---------|
| `main_menu_view()` | The bot's main menu |
| `settings_view()` | User settings screen |
| `create_meeting_view()` | Meeting creation prompt |
| `request_information_with_cancel_view()` | Ask user for input with a Cancel button |
| `change_settings_element_view()` | Settings input with Cancel back to settings |
| `edit_meeting_property_view()` | Edit a meeting field with a Back button |
| `edit_meeting_date_view()` | Calendar-based date selection for a meeting |
| `settings_set_language_view()` | Language selection from settings |
| `meeting_set_language_view()` | Language selection for a meeting |
| `confirmation_view()` | Yes/no confirmation dialog — MUST use for any accept/decline flow |
| `options_button()` | Toggle button showing ✅ or 🔴 based on current state |
| `user_button()` | Button representing a user (for kick-out, invitation lists) |
| `reactivation_prompt_view()` | Prompt shown to meeting owner when their inactive meeting is accessed |

## Usage

Import from `mitup_bot.views.factory`:

```python
from mitup_bot.views import factory

view = factory.confirmation_view(
    lang=user.lang,
    message=MeetingMessages.CONFIRM_DELETE.get(lang=user.lang),
    confirm_callback=cb.CONFIRM_DELETE_MEETING.with_id(meeting_id),
    decline_callback=cb.DECLINE_DELETE_MEETING.with_id(meeting_id),
)
```

All factory functions are stateless and take keyword arguments. Inspect the function signature in `factory.py` for the exact parameters — they vary by screen type.
