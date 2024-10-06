import gettext
from pathlib import Path

SUPPORTED_LANGUAGES = ["en", "es"]


class TranslationEngine:
    """This class is a wrapper arroung gettext that provides localization capabilities to MitupContext."""

    LOCALES_DIR = Path(__file__).parent / "locales"
    DOMAIN = "mitup_bot"
    FALLBACK_LANG = "en"

    translations: dict[str, gettext.GNUTranslations] = {}

    def __init__(self):
        self.translations: dict[str, gettext.GNUTranslations] = {}

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
