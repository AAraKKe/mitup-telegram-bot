from mitup_bot.keyboards import Keyboard
from mitup_bot.models import MeetupLocation
from mitup_bot.utils import ButtonMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting_text import maps_url
from tests.helpers import create_meetup, create_settings, create_user


def meeting_with_location(location: MeetupLocation, lang: str = "en"):
    owner = create_user(id=1, settings=create_settings(id=1, language=lang))
    return create_meetup(id=1, title="Test Meeting", location=location, owner=owner, language=lang)


def url_buttons(keyboard: Keyboard) -> list[str]:
    return [button.url for row in keyboard for button in row if button.url is not None]


def test_maps_url_flips_coordinates_from_lng_lat_to_lat_lng():
    # coordinates are stored (longitude, latitude); the query must read latitude,longitude
    location = MeetupLocation(coordinates=(2.34, 48.85))

    assert maps_url(location) == "https://www.google.com/maps/search/?api=1&query=48.85%2C2.34"


def test_maps_url_prefers_coordinates_over_name():
    location = MeetupLocation(name="Ignored", coordinates=(2.34, 48.85))

    assert maps_url(location) == "https://www.google.com/maps/search/?api=1&query=48.85%2C2.34"


def test_maps_url_uses_name_when_no_coordinates():
    location = MeetupLocation(name="Central Park")

    assert maps_url(location) == "https://www.google.com/maps/search/?api=1&query=Central+Park"


def test_maps_url_percent_encodes_special_characters():
    location = MeetupLocation(name="Café & Bar, Berlin")

    assert maps_url(location) == "https://www.google.com/maps/search/?api=1&query=Caf%C3%A9+%26+Bar%2C+Berlin"


def test_maps_url_is_none_when_location_empty():
    assert maps_url(MeetupLocation()) is None


def test_maps_url_is_none_for_blank_name():
    assert maps_url(MeetupLocation(name="   ")) is None


def test_main_view_shows_maps_button_when_location_set(lang: str):
    meeting = meeting_with_location(MeetupLocation(coordinates=(2.34, 48.85)), lang=lang)

    view = meeting_views.main_view(meeting)

    assert maps_url(meeting.location) in url_buttons(view.keyboard)


def test_main_view_omits_maps_button_when_location_empty(lang: str):
    meeting = meeting_with_location(MeetupLocation(), lang=lang)

    view = meeting_views.main_view(meeting)

    assert url_buttons(view.keyboard) == []


def test_main_view_maps_button_uses_open_in_maps_label(lang: str):
    meeting = meeting_with_location(MeetupLocation(name="Central Park"), lang=lang)

    view = meeting_views.main_view(meeting)
    maps_buttons = [button for row in view.keyboard for button in row if button.url is not None]

    assert len(maps_buttons) == 1
    assert maps_buttons[0].text == ButtonMessages.OPEN_IN_MAPS.get_text(lang=lang)


def test_external_view_shows_maps_button_when_location_set(lang: str):
    meeting = meeting_with_location(MeetupLocation(coordinates=(2.34, 48.85)), lang=lang)

    view = meeting_views.external_view(meeting)

    assert maps_url(meeting.location) in url_buttons(view.keyboard)


def test_external_view_omits_maps_button_when_location_empty(lang: str):
    meeting = meeting_with_location(MeetupLocation(), lang=lang)

    view = meeting_views.external_view(meeting)

    assert url_buttons(view.keyboard) == []


def test_inline_keyboard_shows_maps_button_when_location_set(lang: str):
    meeting = meeting_with_location(MeetupLocation(name="Central Park"), lang=lang)

    keyboard = meeting_views.build_inline_keyboard(meeting)

    assert maps_url(meeting.location) in url_buttons(keyboard)


def test_inline_keyboard_omits_maps_button_when_location_empty(lang: str):
    meeting = meeting_with_location(MeetupLocation(), lang=lang)

    keyboard = meeting_views.build_inline_keyboard(meeting)

    assert url_buttons(keyboard) == []
