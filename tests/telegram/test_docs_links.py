import pytest

from mitup_bot import docs_links


def test_configure_replaces_the_active_base_url():
    original = docs_links.DocsState.base_url
    try:
        docs_links.configure("bot.staging.mitup.social")
        assert docs_links.DocsState.base_url == "https://staging.mitup.social"
    finally:
        docs_links.DocsState.base_url = original


@pytest.mark.parametrize(
    "bot_domain,expected",
    [
        (None, "https://mitup.social"),
        ("", "https://mitup.social"),
        ("   ", "https://mitup.social"),
        ("bot.mitup.social", "https://mitup.social"),
        ("bot.staging.mitup.social", "https://staging.mitup.social"),
        ("example.com", "https://example.com"),
    ],
    ids=["polling-no-domain", "empty", "whitespace", "prod", "staging", "no-bot-prefix"],
)
def test_base_url_for_domain(bot_domain: str | None, expected: str):
    assert docs_links.base_url_for_domain(bot_domain) == expected


@pytest.fixture
def configured_base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    base_url = "https://docs.example.test"
    monkeypatch.setattr(docs_links.DocsState, "base_url", base_url)
    return base_url


def test_user_guide_url_builds_on_the_configured_base(configured_base_url: str):
    assert docs_links.user_guide_url() == f"{configured_base_url}/user-guide/"


def test_privacy_url_builds_on_the_configured_base(configured_base_url: str):
    assert docs_links.privacy_url() == f"{configured_base_url}/faq/privacy/"


def test_default_base_url_is_the_production_site():
    assert docs_links.DEFAULT_BASE_URL == "https://mitup.social"


def test_user_guide_url_defaults_to_the_production_site(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docs_links.DocsState, "base_url", docs_links.DEFAULT_BASE_URL)

    assert docs_links.user_guide_url() == "https://mitup.social/user-guide/"


def test_privacy_url_defaults_to_the_production_site(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docs_links.DocsState, "base_url", docs_links.DEFAULT_BASE_URL)

    assert docs_links.privacy_url() == "https://mitup.social/faq/privacy/"
