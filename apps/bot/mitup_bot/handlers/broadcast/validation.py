from dataclasses import dataclass

import structlog
import yaml

from mitup_bot.translations import SUPPORTED_LANGUAGES, TranslationEngine
from mitup_bot.utils.entities import parse_format_tags, utf16_len
from mitup_bot.utils.messages import BroadcastOperatorMessages

log = structlog.get_logger(__name__)

# Telegram's sendMessage caps a single message at 4096 UTF-16 code units, measured on the rendered
# text (tags do not count toward the limit).
MAX_MESSAGE_UTF16_LENGTH = 4096

# Substitutions that quote the uploaded document back at the operator. They belong in the reply,
# never on a log line: the parser detail embeds the offending fragment of the message body.
FREE_TEXT_PARAMS = frozenset({"detail"})


class BroadcastContentError(ValueError):
    """Fatal, operator-actionable validation failure.

    Carries the message template plus its substitutions so the handler can render a specific,
    actionable reply and keep the operator on the upload step.
    """

    def __init__(self, message: BroadcastOperatorMessages, **params: str | int):
        self.message = message
        self.params = params
        super().__init__(f"{message.name}: {params}")

    @property
    def reason(self) -> str:
        """The failure as a bounded machine value — one per `BroadcastOperatorMessages` error."""
        return self.message.name.lower()

    @property
    def log_params(self) -> dict[str, str | int]:
        return {name: value for name, value in self.params.items() if name not in FREE_TEXT_PARAMS}


@dataclass(frozen=True)
class BroadcastLanguageContent:
    language: str
    body_html: str
    char_count: int


@dataclass(frozen=True)
class ValidatedBroadcast:
    messages: list[BroadcastLanguageContent]
    skipped_languages: list[str]

    @property
    def english_body(self) -> str:
        return next(
            content.body_html for content in self.messages if content.language == TranslationEngine.FALLBACK_LANG
        )


def strip_html(body: str) -> str:
    """Return the visible text of *body*, dropping the supported Telegram HTML tags."""
    return parse_format_tags(body, {}).text


def parse_and_validate(raw: str) -> ValidatedBroadcast:
    """Parse the operator's YAML into a validated set of per-language messages.

    Unknown language codes are skipped as warnings; a missing English fallback, a duplicate
    language, or a malformed message is fatal and raises `BroadcastContentError`.
    """
    entries = load_entries(raw)
    contents: list[BroadcastLanguageContent] = []
    skipped_languages: list[str] = []
    seen: set[str] = set()

    for index, entry in enumerate(entries):
        language, body = entry_fields(entry, index)
        if language not in SUPPORTED_LANGUAGES:
            # A typo'd locale code removes a whole language from the send while the upload still
            # succeeds, and the operator only sees it as one warning line among the previews.
            log.warning(
                "Skipped unsupported broadcast language",
                stage="validate",
                language=language,
                position=index + 1,
                reason="unsupported_language_code",
            )
            skipped_languages.append(language)
            continue
        if language in seen:
            raise BroadcastContentError(BroadcastOperatorMessages.ERROR_DUPLICATE_LANGUAGE, language=language)
        seen.add(language)
        contents.append(validated_content(language, body))

    if TranslationEngine.FALLBACK_LANG not in seen:
        raise BroadcastContentError(
            BroadcastOperatorMessages.ERROR_MISSING_ENGLISH, language=TranslationEngine.FALLBACK_LANG
        )
    log.info(
        "Broadcast content validated",
        stage="validate",
        outcome="accepted",
        languages=[content.language for content in contents],
        char_counts={content.language: content.char_count for content in contents},
        skipped_languages=skipped_languages,
        entry_count=len(entries),
    )
    return ValidatedBroadcast(messages=contents, skipped_languages=skipped_languages)


def load_entries(raw: str) -> list[object]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_INVALID_YAML, detail=yaml_detail(error)) from error
    if not isinstance(data, list):
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_NOT_A_LIST)
    if not data:
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_EMPTY_LIST)
    return data


def entry_fields(entry: object, index: int) -> tuple[str, str]:
    if not isinstance(entry, dict) or set(entry) != {"language", "message"}:
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_ENTRY_SHAPE, position=index + 1)
    language = entry.get("language")
    body = entry.get("message")
    if not isinstance(language, str) or not isinstance(body, str):
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_ENTRY_SHAPE, position=index + 1)
    return language, body


def validated_content(language: str, body: str) -> BroadcastLanguageContent:
    # Validate against the rendered text: unsupported tags are dropped by parse_format_tags (the
    # visual preview is the safety net), and Telegram's length limit is measured on visible text.
    rendered = parse_format_tags(body, {})
    if not rendered.text.strip():
        raise BroadcastContentError(BroadcastOperatorMessages.ERROR_EMPTY_MESSAGE, language=language)
    length = utf16_len(rendered.text)
    if length > MAX_MESSAGE_UTF16_LENGTH:
        raise BroadcastContentError(
            BroadcastOperatorMessages.ERROR_MESSAGE_TOO_LONG,
            language=language,
            length=length,
            limit=MAX_MESSAGE_UTF16_LENGTH,
        )
    return BroadcastLanguageContent(language=language, body_html=body, char_count=length)


def yaml_detail(error: yaml.YAMLError) -> str:
    problem = getattr(error, "problem", None)
    return str(problem) if problem else str(error).splitlines()[0]
