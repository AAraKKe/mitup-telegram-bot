"""PTB-free primitives of the format-tag dialect.

`TOKEN_RE` is the single tokenizer for tag-annotated strings: the entity parser in
`mitup_bot.utils.entities` and `strip_format_tags` both consume it, so the visible text of a
tagged string is identical whichever path reads it.
"""

import html
import re

# The substitution placeholder written in every message value and every translated catalog entry.
# Spelled once here because two readers need it apart from the tokenizer: the rich renderer, and
# the catalog check that compares a translation's placeholders against its English source.
PLACEHOLDER_PATTERN = r"\$\{(?P<var>\w+)\}"
PLACEHOLDER_RE = re.compile(PLACEHOLDER_PATTERN)

# A tag is `<name attr="value" ...>` or a closing `</name>`; attributes are optional
# `name="value"` (or single-quoted) pairs. The `${var}` alternative matches substitution
# placeholders. Tag names require a leading letter, so `<3`, a bare `<`, and `<https://…>`
# never match and are preserved verbatim.
TOKEN_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>[a-z][a-z-]*)"
    r"""(?P<attrs>(?:\s+[a-zA-Z-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*>"""
    rf"|{PLACEHOLDER_PATTERN}"
)


# Every tag name the dialect admits. The styles carry no attribute and differ only in the alias
# used to write them; the three below them each depend on an attribute, which the renderers read.
# The vocabulary lives here rather than beside the renderer so the catalog check can reject a tag
# nobody can render without importing the Telegram layer to learn the names.
STYLE_TAG_NAMES = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "spoiler", "tg-spoiler", "blockquote"}
)
ATTRIBUTE_TAG_NAMES = frozenset({"a", "span", "tg-emoji"})
FORMAT_TAG_NAMES = STYLE_TAG_NAMES | ATTRIBUTE_TAG_NAMES


def placeholder_names(text: str) -> set[str]:
    """Return the names of the `${var}` placeholders *text* carries."""
    return {match.group("var") for match in PLACEHOLDER_RE.finditer(text)}


def strip_format_tags(tagged: str) -> str:
    """Return the visible plain text of a tag-annotated string.

    Tags are removed, HTML character references in the literal runs are decoded, and `${var}`
    placeholders are kept verbatim — byte-identical to the text `parse_format_tags` produces
    with no substitutions.
    """
    parts: list[str] = []
    cursor = 0
    for token in TOKEN_RE.finditer(tagged):
        parts.append(html.unescape(tagged[cursor : token.start()]))
        cursor = token.end()
        if token.group("var") is not None:
            parts.append(token.group(0))
    parts.append(html.unescape(tagged[cursor:]))
    return "".join(parts)
