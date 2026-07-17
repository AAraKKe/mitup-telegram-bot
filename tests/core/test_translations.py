import pytest

from mitup_bot.translations import locale_for_language_code


@pytest.mark.parametrize(
    ("language_code", "expected_locale"),
    [
        ("en", "en"),
        ("es", "es_ES"),
        ("es-MX", "es_ES"),
        ("es-mx", "es_ES"),
        ("pt-BR", "pt_BR"),
        ("pt-br", "pt_BR"),
        ("de", "de_DE"),
        ("it", "it_IT"),
        ("gl", "gl_ES"),
        ("fr", "en"),
        (None, "en"),
    ],
)
def test_locale_for_language_code_maps_to_supported_locale_or_fallback(language_code: str | None, expected_locale: str):
    assert locale_for_language_code(language_code) == expected_locale
