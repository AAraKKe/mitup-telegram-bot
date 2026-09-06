import pytest

from mitup_bot import bot_links


def test_a_start_link_opens_the_bot_carrying_its_source():
    assert bot_links.start_link("sharedcard") == "https://t.me/mitupbot?start=sharedcard"


def test_a_configured_username_is_the_one_the_links_point_at():
    bot_links.configure("mitupstagingbot")

    assert bot_links.start_link("meetingcard") == "https://t.me/mitupstagingbot?start=meetingcard"


@pytest.mark.parametrize("username", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_deployment_that_names_no_bot_keeps_the_production_one(username: str | None):
    """A link to the wrong bot is worse than one to the right bot from a deployment that forgot to
    say which it is."""
    bot_links.configure(username)

    assert bot_links.BotLinkState.username == bot_links.DEFAULT_USERNAME
