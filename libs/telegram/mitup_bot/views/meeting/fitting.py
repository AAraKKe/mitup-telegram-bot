from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable

from mitup_bot.utils.rich_message import MAX_RICH_TEXT_LENGTH, RichContent

# What a card body may spend of the wire ceiling. A rendered card carries chrome the body does not:
# the owner's is prepended a state banner, a shared one is closed by its footers, and every card
# ends on its keyboard. Telegram counts the text of all of it against the same ceiling, so the body
# stops short of it by enough to hold them.
CARD_CHROME_RESERVE = 1024
MEETING_CARD_BUDGET = MAX_RICH_TEXT_LENGTH - CARD_CHROME_RESERVE

# How a card body renders when it names only the first *shown* guests; None names every one.
CardBody = Callable[[int | None], RichContent]


def fitted_body(render: CardBody, named: int) -> RichContent:
    """Render a card body, naming as many of its *named* guests as the budget has room for.

    The names are the one part of a card that grows without bound, so they are what gives way.
    Below the full list every rendering carries the line counting the names left out, so the body's
    length grows strictly with the number named and the largest count that fits is a bisection.
    """
    body = render(None)
    if body.text_length <= MEETING_CARD_BUDGET:
        return body
    counts = range(named)
    fitting = bisect_right(counts, MEETING_CARD_BUDGET, key=lambda shown: render(shown).text_length)
    return render(max(fitting - 1, 0))
