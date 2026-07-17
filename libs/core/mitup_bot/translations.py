import gettext
from pathlib import Path

SUPPORTED_LANGUAGES = ["en", "es_ES", "gl_ES", "de_DE", "pt_BR", "it_IT"]


class TranslationEngine:
    """This class is a wrapper arroung gettext that provides localization capabilities to MitupContext."""

    LOCALES_DIR = Path(__file__).parent / "locales"
    DOMAIN = "mitup_bot"
    FALLBACK_LANG = "en"

    translations: dict[str, gettext.GNUTranslations] = {}

    @classmethod
    def __load_translation(cls, lang: str):
        cls.translations[lang] = gettext.translation(
            domain=cls.DOMAIN, localedir=cls.LOCALES_DIR, languages=[lang, cls.FALLBACK_LANG]
        )

    @classmethod
    def translate(cls, message_id: str, lang: str) -> str:
        if lang not in cls.translations:
            cls.__load_translation(lang)

        return cls.translations[lang].gettext(message_id)


# Each supported locale has a unique primary subtag ("es" → "es_ES", "pt" → "pt_BR", ...), so a
# Telegram language_code can be matched on its primary subtag alone.
LANGUAGE_CODE_TO_LOCALE = {locale.split("_")[0]: locale for locale in SUPPORTED_LANGUAGES}


def locale_for_language_code(language_code: str | None) -> str:
    """Map a Telegram `language_code` to a supported locale, falling back to English.

    Telegram sends the user's client language as an optional IETF BCP-47 tag like "en", "es-MX" or
    "pt-br". Matching is on the primary subtag only, so "es" and "es-MX" both resolve to "es_ES".
    An absent or unsupported tag yields the English fallback locale.
    """
    if language_code is None:
        return TranslationEngine.FALLBACK_LANG
    primary_subtag = language_code.lower().split("-")[0]
    return LANGUAGE_CODE_TO_LOCALE.get(primary_subtag, TranslationEngine.FALLBACK_LANG)
