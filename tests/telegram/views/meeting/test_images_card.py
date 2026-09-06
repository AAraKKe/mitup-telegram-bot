from mitup_bot import limits
from mitup_bot.images import ImageLayout
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingImagesMessages
from mitup_bot.views.context import RenderContext
from mitup_bot.views.meeting import banner, images_card
from mitup_bot.views.mitup_view import MitupView
from tests.telegram.views.meeting.helpers import owned_meeting, with_images


def images_meeting(count: int, layout: ImageLayout = ImageLayout.COLLAGE) -> Meetup:
    return with_images(owned_meeting(), count, layout)


def screen_lines(count: int) -> list[str]:
    return images_card.images_view(images_meeting(count)).message.text.splitlines()


def banner_html(meeting: Meetup) -> str:
    drawn = banner.banner_content(meeting)
    assert drawn is not None
    return drawn.html


def media_ids(view: MitupView) -> list[str]:
    return [entry["id"] for entry in view.rich_message().to_api_dict().get("media", [])]


# --- The banner every card opens with ---


def test_a_meeting_with_no_photos_draws_no_banner():
    assert banner.banner_content(images_meeting(0)) is None


def test_a_single_photo_is_drawn_as_itself_rather_than_as_an_arrangement():
    """A collage or a slideshow needs at least two photos."""
    assert banner_html(images_meeting(1)) == '<img src="tg://photo?id=uniq-0"/>'


def test_several_photos_are_drawn_in_the_arrangement_the_meeting_holds():
    collage = banner_html(images_meeting(3, ImageLayout.COLLAGE))
    slideshow = banner_html(images_meeting(3, ImageLayout.SLIDESHOW))

    assert collage.startswith("<tg-collage>") and collage.endswith("</tg-collage>")
    assert slideshow.startswith("<tg-slideshow>") and slideshow.endswith("</tg-slideshow>")


def test_the_banner_draws_the_photos_in_the_order_the_meeting_holds_them():
    html = banner_html(images_meeting(3))

    assert html.index("uniq-0") < html.index("uniq-1") < html.index("uniq-2")


def test_the_photos_a_surface_carries_name_both_ids_telegram_knows_a_photo_by():
    photos = banner.meeting_photos(images_meeting(2))

    assert [(photo.media_id, photo.file_id) for photo in photos] == [
        ("uniq-0", "file-0"),
        ("uniq-1", "file-1"),
    ]


# --- The screen the photos are managed on ---


def test_the_screen_opens_on_its_title_and_the_lead_that_says_what_to_send(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 0)

    body = images_card.images_view(meeting).message

    assert body.html.startswith("<h2>")
    assert MeetingImagesMessages.TITLE.rich(lang=lang).text in body.text
    assert MeetingImagesMessages.LEAD.rich(lang=lang, limit=limits.MEETING_IMAGES_MAX).text in body.text


def test_the_lead_names_the_number_of_photos_a_meeting_holds():
    assert str(limits.MEETING_IMAGES_MAX) in "\n".join(screen_lines(0))


def test_an_empty_screen_offers_no_photo_rows_and_no_arrangement():
    html = images_card.images_view(images_meeting(0)).message.html

    assert "<img" not in html
    assert str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=0)) not in html


def test_the_photos_are_shown_as_a_slideshow_whatever_the_card_arranges_them_as():
    """A slideshow shows the photos one by one, in the order the rows under it number them."""
    html = images_card.images_view(images_meeting(3, ImageLayout.COLLAGE)).message.html

    assert "<tg-slideshow>" in html
    assert "<tg-collage>" not in html


def test_every_photo_gets_a_numbered_row_counting_from_one(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 3)

    text = images_card.images_view(meeting).message.text

    for position in range(3):
        assert MeetingImagesMessages.IMAGE_LABEL.rich(lang=lang, number=position + 1).text in text


def test_each_photo_row_carries_the_chips_acting_on_that_photo_alone():
    html = images_card.images_view(images_meeting(3)).message.html

    for position in range(3):
        assert str(cb.REPLACE_MEETING_IMAGE.with_ids(meeting_id=7, id=position)) in html
        assert str(cb.DELETE_MEETING_IMAGE.with_ids(meeting_id=7, id=position)) in html


def test_a_photo_row_closes_on_its_removal_chip():
    """The Remove chip closes the row, as on every other row of the card."""
    row = images_card.image_row(images_meeting(1), 0).html

    assert row.index('style="danger"') > row.index(str(cb.REPLACE_MEETING_IMAGE.with_ids(meeting_id=7, id=0)))


def test_a_single_photo_offers_neither_a_clear_all_chip_nor_an_arrangement():
    """Both act on photos as a group, and one photo is not a group."""
    html = images_card.images_view(images_meeting(1)).message.html

    assert str(cb.DELETE_MEETING_IMAGES.with_id(7)) not in html
    assert str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=0)) not in html


def test_two_photos_bring_the_clear_all_chip_and_the_arrangement_picker():
    html = images_card.images_view(images_meeting(2)).message.html

    assert str(cb.DELETE_MEETING_IMAGES.with_id(7)) in html
    assert str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=0)) in html
    assert str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=1)) in html


def test_the_clear_all_chip_sits_below_every_photo_row():
    """It acts on all of them, so it closes the list rather than sitting inside it."""
    html = images_card.photo_list(images_meeting(3)).html

    assert html.index(str(cb.DELETE_MEETING_IMAGES.with_id(7))) > html.index(
        str(cb.DELETE_MEETING_IMAGE.with_ids(meeting_id=7, id=2))
    )


def test_the_arrangement_in_force_is_the_accented_button():
    html = images_card.images_view(images_meeting(2, ImageLayout.SLIDESHOW)).message.html
    collage = str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=0))
    slideshow = str(cb.SET_MEETING_IMAGE_LAYOUT.with_ids(meeting_id=7, id=1))

    assert f'data="{slideshow}" style="primary"' in html
    assert f'data="{collage}" style="primary"' not in html


def test_the_arrangement_buttons_share_one_full_width_row():
    """A button row stretches to the full width; a chip inside a line is drawn at a fixed size."""
    html = images_card.layout_section(images_meeting(2)).html

    assert html.count("<tg-button-row>") == 1


def test_the_screen_says_which_photo_the_next_one_replaces(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 3)

    text = images_card.images_view(meeting, replacing=1).message.text

    assert MeetingImagesMessages.REPLACING.rich(lang=lang, number=2).text in text


def test_the_replacement_line_stands_above_the_screen_it_comments_on():
    html = images_card.images_view(images_meeting(3), replacing=0).message.html

    assert html.index(MeetingImagesMessages.REPLACING.rich(lang="en", number=1).text) < html.index("<h2>")


def test_the_screen_closes_on_the_way_back_to_the_meeting(lang: str):
    """The back button cancels the photo flow, so a photo sent afterwards is not taken as one more
    photo for this meeting."""
    meeting = with_images(owned_meeting(lang=lang), 3)

    back = images_card.images_view(meeting).menu[-1]

    assert [button.text for button in back] == [ButtonMessages.MEETING.back(lang=lang)]
    assert [str(button.callback_data) for button in back] == [str(cb.EDIT_MEETING_CANCEL.with_id(meeting.db_id))]


# --- The same screen for an owner who is not a Host ---


def test_the_locked_screen_keeps_the_title_and_says_what_photos_are_for(lang: str):
    meeting = owned_meeting(lang=lang)

    body = images_card.images_locked_view(meeting).message

    assert body.html.startswith("<h2>")
    assert MeetingImagesMessages.LOCKED.rich(lang=lang).text in body.text


def test_the_locked_screen_offers_the_way_to_become_a_host(lang: str):
    meeting = owned_meeting(lang=lang)

    menu = images_card.images_locked_view(meeting).menu

    assert [str(button.callback_data) for row in menu for button in row] == [
        str(cb.COLLABORATE),
        str(cb.EDIT_MEETING.with_id(meeting.db_id)),
    ]


def test_the_locked_screen_offers_the_chip_that_clears_the_photos(lang: str):
    """Photos added while the owner was a Host stay after the plan lapses, so removing them cannot need it."""
    view = images_card.images_locked_view(with_images(owned_meeting(lang=lang), 2))

    assert str(cb.DELETE_MEETING_IMAGES.with_id(7)) in view.message.html
    assert ButtonMessages.REMOVE_ALL.text(lang=lang) in view.message.text
    assert str(cb.DELETE_MEETING_IMAGES.with_id(7)) not in [
        str(button.callback_data) for row in view.menu for button in row
    ]


def test_the_chip_that_clears_the_photos_closes_the_paragraph_explaining_them():
    """It sits in the body, above the keyboard that leaves the screen."""
    view = images_card.images_locked_view(images_meeting(2))
    html = view.message.html

    assert html.index(str(cb.DELETE_MEETING_IMAGES.with_id(7))) > html.index("</h2>")


def test_a_locked_screen_with_nothing_to_clear_offers_no_chip():
    view = images_card.images_locked_view(images_meeting(0))

    assert str(cb.DELETE_MEETING_IMAGES.with_id(7)) not in view.message.html


def test_the_locked_screen_shows_no_photos():
    """Nothing was attached, so there is nothing to draw and nothing to carry."""
    view = images_card.images_locked_view(with_images(owned_meeting(), 0))

    assert "<img" not in view.message.html
    assert media_ids(view) == []


# --- What every screen showing photos has to carry ---


def test_the_screen_carries_every_photo_it_draws():
    view = images_card.images_view(images_meeting(3))

    assert media_ids(view) == ["uniq-0", "uniq-1", "uniq-2"]


# --- The prompts before photos are removed ---


def test_the_removal_prompt_shows_the_photo_about_to_go_above_the_question(lang: str):
    """A number alone would leave the owner guessing which photo it names."""
    meeting = with_images(owned_meeting(lang=lang), 3)

    view = images_card.remove_image_prompt(RenderContext(lang=lang), meeting, 1)

    assert view.message.html.startswith('<img src="tg://photo?id=uniq-1"/>')
    assert MeetingImagesMessages.REMOVE_CONFIRMATION.rich(lang=lang, number=2).text in view.message.text
    assert media_ids(view) == ["uniq-1"]


def test_the_removal_prompt_confirms_and_declines_on_that_photo_alone():
    view = images_card.remove_image_prompt(RenderContext(lang="en"), images_meeting(3), 1)

    buttons = [button for row in view.menu for button in row]
    assert [button.callback_data for button in buttons] == [
        cb.CONFIRM_DELETE_MEETING_IMAGE.with_ids(meeting_id=7, id=1),
        cb.DECLINE_DELETE_MEETING_IMAGE.with_ids(meeting_id=7, id=1),
    ]


def test_the_remove_all_prompt_carries_no_photo(lang: str):
    meeting = with_images(owned_meeting(lang=lang), 3)

    view = images_card.remove_all_images_prompt(RenderContext(lang=lang), meeting)

    assert view.message.text == MeetingImagesMessages.REMOVE_ALL_CONFIRMATION.rich(lang=lang).text
    assert media_ids(view) == []
    buttons = [button for row in view.menu for button in row]
    assert [button.callback_data for button in buttons] == [
        cb.CONFIRM_DELETE_MEETING_IMAGES.with_id(7),
        cb.DECLINE_DELETE_MEETING_IMAGES.with_id(7),
    ]
