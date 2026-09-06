from __future__ import annotations

from mitup_bot.emojis import Emojis
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.messages import MessageBase
from mitup_bot.utils.rich_message import RichContent, RichTag
from mitup_bot.utils.rich_template import render_rich


def section_header(title: MessageBase, glyph: Emojis, lang: str, *, trailer: RichContent | None = None) -> RichContent:
    """Bold rather than a heading tag: clients draw every heading in a serif face with no font
    control."""
    title_text = title.rich(lang=lang).wrap(RichTag.BOLD)
    header = render_rich(t"{glyph} {title_text}")
    if trailer is None:
        return header
    return render_rich(t"{header} · {trailer}")


def card_section(
    title: MessageBase,
    glyph: Emojis,
    lang: str,
    body: RichContent,
    *,
    trailer: RichContent | None = None,
) -> RichContent:
    return section_header(title, glyph, lang, trailer=trailer).append("\n").append(body)


def chip_section(title: MessageBase, glyph: Emojis, lang: str, chip: ButtonConfig, body: RichContent) -> RichContent:
    """A section whose chip rides the title line, acting on the section as a whole."""
    header = section_header(title, glyph, lang)
    return render_rich(t"{header} {chip}").append("\n").append(body)
