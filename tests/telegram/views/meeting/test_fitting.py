from mitup_bot.utils.rich_message import MAX_RICH_TEXT_LENGTH, RichContent
from mitup_bot.views.meeting.fitting import CARD_CHROME_RESERVE, MEETING_CARD_BUDGET, fitted_body

NAMES = 200


def body(shown: int | None) -> RichContent:
    """A body whose length grows strictly with the number of names it renders, as a card's does."""
    named = NAMES if shown is None else shown
    return RichContent.join("\n", [f"Name {index}" for index in range(named)] + [f"{NAMES - named} more"])


def test_a_body_that_fits_is_rendered_as_it_comes_out():
    assert fitted_body(body, NAMES) == body(None)


def test_an_over_budget_body_names_as_many_as_the_budget_holds():
    """Room for five names and no more, so the fitted body names five and counts the rest."""
    padding = MEETING_CARD_BUDGET - body(5).text_length

    def crowded(shown: int | None) -> RichContent:
        return body(shown).append("x" * padding)

    fitted = fitted_body(crowded, NAMES)

    assert fitted.text_length <= MEETING_CARD_BUDGET
    assert fitted == crowded(5)


def test_a_body_that_cannot_fit_even_empty_bottoms_out_at_no_names():
    """A card that dropped the section outright would read as one nobody had joined, so the count
    of the names left out is the last thing given up."""

    def overlong(shown: int | None) -> RichContent:
        return body(shown).append("x" * MEETING_CARD_BUDGET)

    assert fitted_body(overlong, NAMES) == overlong(0)


def test_the_budget_leaves_the_wire_ceiling_room_for_a_banner_and_a_keyboard():
    """Telegram counts the text of both against the same ceiling as the body, and neither is part
    of what a card measures when it fits itself."""
    assert MEETING_CARD_BUDGET == MAX_RICH_TEXT_LENGTH - CARD_CHROME_RESERVE
    assert CARD_CHROME_RESERVE > 0
