"""PTB-free primitives of the format-tag dialect.

`TOKEN_RE` is the single tokenizer for tag-annotated strings: the entity parser in
`mitup_bot.utils.entities` and `strip_format_tags` both consume it, so the visible text of a
tagged string is identical whichever path reads it.
"""

import html
import re

# A tag is `<name attr="value" ...>` or a closing `</name>`; attributes are optional
# `name="value"` (or single-quoted) pairs. The `${var}` alternative matches substitution
# placeholders. Tag names require a leading letter, so `<3`, a bare `<`, and `<https://…>`
# never match and are preserved verbatim.
TOKEN_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>[a-z][a-z-]*)"
    r"""(?P<attrs>(?:\s+[a-zA-Z-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*>"""
    r"|\$\{(?P<var>\w+)\}"
)


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
