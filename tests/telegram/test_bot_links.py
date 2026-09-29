import pytest

from mitup_bot import bot_links
from mitup_bot.acquisition import BOT_CARD_FOOTER_PAYLOAD, SHARED_CARD_FOOTER_PAYLOAD


def test_a_start_link_opens_the_bot_carrying_its_source():
    assert bot_links.start_link(SHARED_CARD_FOOTER_PAYLOAD) == "https://t.me/mitupbot?start=src_footer"


def test_a_configured_username_is_the_one_the_links_point_at():
    bot_links.configure("mitupstagingbot")

    assert bot_links.start_link(BOT_CARD_FOOTER_PAYLOAD) == "https://t.me/mitupstagingbot?start=src_botcard"


@pytest.mark.parametrize("username", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_deployment_that_names_no_bot_keeps_the_production_one(username: str | None):
    """A link to the wrong bot is worse than one to the right bot from a deployment that forgot to
    say which it is."""
    bot_links.configure(username)

    assert bot_links.BotLinkState.username == bot_links.DEFAULT_USERNAME
