"""Turn a dumped screen (scratchpad/screens/<name>.txt) into a docs chat showcase.

rich_to_doc() translates the bot's rich-message markup tag by tag; annotated()/phone() wrap it.
Annotations are declared as (marker, label, side): the marker is a substring of the translated
html; a data-note attribute is planted on the element that contains it and measure.py later sets
the `top` of each annotation from the built page.
"""

import re
import sys
from pathlib import Path

SCREENS = Path(__file__).parent / "screens"
BUBBLE_OPEN = (
    '<div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div>'
)
SLIDESHOW_PHOTO = (
    '<div class="mitup-card__photos mitup-card__photos--slideshow"><div class="mitup-card__photo"></div></div>'
)
AVATAR = '<div class="mitup-avatar"><img src="../../assets/images/brand/mark-256.png" alt="Mitup"></div>'
HEADER = f"""    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      {AVATAR}
      <div>
        <div class="mitup-chat-header__name">mitupbot</div>
        <div class="mitup-chat-header__sub">bot · online</div>
      </div>
    </div>"""
INPUT_BAR = """    <div class="mitup-chat-input">
      <div class="mitup-chat-input__menu">≡</div>
      <span class="mitup-chat-input__attach">📎</span>
      <span class="mitup-chat-input__placeholder">Write a message…</span>
    </div>"""


def load(name: str) -> tuple[str, list[list[tuple[str, str | None]]], list[str]]:
    text = (SCREENS / f"{name}.txt").read_text()
    html = text.split("--- HTML ---\n", 1)[1].split("\n\n--- MENU", 1)[0].strip()
    menu_block = text.split("--- MENU ---\n", 1)[1]
    rows = []
    for line in menu_block.splitlines():
        if not line.startswith("["):
            break
        rows.append(
            [(m.group(1), m.group(2)) for m in re.finditer(r"\[([^\]]*)\](?:\(style=(\w+)\))?(?:\(disabled\))?", line)]
        )
    photos = re.findall(r"--- PHOTOS --- \[(.*?)\]", text)
    return html, rows, photos


def key(label: str, style: str | None, rich: bool, disabled: bool = False) -> str:
    classes = ["mitup-key"]
    if rich:
        classes.append("mitup-key--rich")
    if style:
        classes.append(f"mitup-key--{style}")
    if disabled:
        classes.append("mitup-key--disabled")
    return f'<div class="{" ".join(classes)}">{label}</div>'


def row(keys: list[str]) -> str:
    cols = f' style="--cols: {len(keys)}"' if len(keys) > 1 else ""
    return f'<div class="mitup-bot-msg__row"{cols}>{"".join(keys)}</div>'


def button_attrs(attrs: str) -> tuple[str | None, bool]:
    style = re.search(r'style="(\w+)"', attrs)
    return (style.group(1) if style else None), 'type="disabled"' in attrs or "disabled" in attrs


def photos_block(kind: str, count: int) -> str:
    if kind == "slideshow":
        dots = "".join(
            '<span class="mitup-card__dot' + (" mitup-card__dot--on" if i == 0 else "") + '"></span>'
            for i in range(count)
        )
        photo = (
            '<div class="mitup-card__photos mitup-card__photos--slideshow"><div class="mitup-card__photo"></div></div>'
        )
        return f'{photo}<div class="mitup-card__dots">{dots}</div>'
    if count == 1:
        return '<div class="mitup-card__photos mitup-card__photos--1"><div class="mitup-card__photo"></div></div>'
    cells = "".join('<div class="mitup-card__photo"></div>' for _ in range(count))
    return f'<div class="mitup-card__photos mitup-card__photos--collage mitup-card__photos--{count}">{cells}</div>'


def rich_to_doc(html: str) -> str:
    def convert_row(m):
        keys = []
        for b in re.finditer(r"<tg-button([^>]*)>(.*?)</tg-button>", m.group(1)):
            style, disabled = button_attrs(b.group(1))
            keys.append(key(b.group(2), style, rich=True, disabled=disabled))
        return row(keys)

    html = re.sub(r"<tg-button-row>(.*?)</tg-button-row>", convert_row, html)

    def convert_chip(m):
        style, disabled = button_attrs(m.group(1))
        classes = "mitup-chip" + (f" mitup-chip--{style}" if style else "") + (" mitup-chip--muted" if disabled else "")
        return f'<span class="{classes}">{m.group(2)}</span>'

    html = re.sub(r"<tg-button([^>]*)>(.*?)</tg-button>", convert_chip, html)
    html = re.sub(r"<h[12]>(.*?)</h[12]>", r'<span class="mitup-card__title">\1</span>', html)
    html = html.replace("<hr/>", '<hr class="mitup-card__rule"/>')
    html = re.sub(r"<footer>(.*?)</footer>", r'<span class="mitup-card__footer">\1</span>', html)
    html = re.sub(r"<tg-time[^>]*>(.*?)</tg-time>", r'<span class="mitup-time">\1</span>', html)
    html = re.sub(r"<tg-map[^>]*/>", '<div class="mitup-card__map"></div>', html)
    html = re.sub(r"<tg-collage>(.*?)</tg-collage>", lambda m: photos_block("collage", m.group(1).count("<img")), html)
    html = re.sub(
        r"<tg-slideshow>(.*?)</tg-slideshow>", lambda m: photos_block("slideshow", m.group(1).count("<img")), html
    )
    html = re.sub(r'<img src="tg://photo[^"]*"/>', photos_block("single", 1), html)
    html = html.replace("<table compact>", '<table class="mitup-card__table">')
    html = re.sub(r"<b>(.*?)</b>", r"<strong>\1</strong>", html)
    html = re.sub(r"<i>(.*?)</i>", r"<em>\1</em>", html)
    # A section title line (glyph + bold word) becomes the section class.
    html = re.sub(
        r"(^|<hr class=\"mitup-card__rule\"/>|<br/>)([^\s<]+ )<strong>([^<]*)</strong>",
        r'\1<span class="mitup-card__section">\2<strong>\3</strong></span>',
        html,
    )
    html = re.sub(
        r"(^|<hr class=\"mitup-card__rule\"/>|<br/>)<strong>([^<]*)</strong>",
        r'\1<span class="mitup-card__section"><strong>\2</strong></span>',
        html,
    )
    return html


def bubble(name: str, *, classic: bool = False, hide_menu: bool = False, extra_rows=(), rename=()) -> str:
    html, rows, _ = load(name)
    rows = rows + list(extra_rows)
    body = rich_to_doc(html)
    for old, new in rename:
        body = body.replace(old, new)
    menu_rows = (
        [row([key(label, style, rich=not classic) for label, style in r]) for r in rows] if not hide_menu else []
    )
    if classic:
        keyboard = f'<div class="mitup-bot-msg__keyboard">{"".join(menu_rows)}</div>' if menu_rows else ""
        return f'{BUBBLE_OPEN}<div class="mitup-bot-msg__text">{body}</div></div>{keyboard}</div>'
    if menu_rows:
        body += '<hr class="mitup-card__rule"/>' + "".join(menu_rows)
    return f'{BUBBLE_OPEN}<div class="mitup-bot-msg__text">{body}</div></div></div>'


VOID_TAGS = ("<br", "<hr", "<img")


def plant_notes(html: str, notes: list[tuple[str, str, str]]) -> str:
    """Mark the element holding *marker* with data-note="<index>"; a marker that sits in loose text
    is wrapped in a span of its own."""
    for index, (marker, _label, _side) in enumerate(notes):
        at = html.index(marker)
        open_tag = html.rfind("<", 0, at)
        while html[open_tag : open_tag + 2] == "</":
            open_tag = html.rfind("<", 0, open_tag)
        gt = html.index(">", open_tag)
        if html.startswith(VOID_TAGS, open_tag) or gt + 1 < at and html[gt + 1 : at].strip():
            html = html[:at] + f'<span data-note="{index}">' + marker + "</span>" + html[at + len(marker) :]
        else:
            html = html[:open_tag] + html[open_tag:gt] + f' data-note="{index}"' + html[gt:]
    return html


def group_header(name: str, members: int, avatar: str) -> str:
    return (
        HEADER.replace(AVATAR, f'<div class="mitup-avatar">{avatar}</div>')
        .replace("mitupbot</div>", f"{name}</div>")
        .replace("bot · online", f"{members} members")
    )


def inline_panel(name: str, rename=()) -> str:
    """The results panel Telegram draws over the keyboard for an empty `@mitupbot` query."""
    rows = []
    for line in (SCREENS / f"{name}.txt").read_text().splitlines():
        kind, _, rest = line.partition("|")
        if kind == "TOP":
            rows.append(f'<div class="mitup-inline-top">{rest}</div>')
        elif kind == "ROW":
            thumb, title, sub = rest.split("|", 2)
            for old, new in rename:
                title, sub = title.replace(old, new), sub.replace(old, new)
            if len(thumb) == 1 and thumb.isalpha():
                thumb = title[0]
            rows.append(
                f'<div class="mitup-inline-result"><div class="mitup-inline-result__thumb">{thumb}</div>'
                f'<div class="mitup-inline-result__body"><div class="mitup-inline-result__title">{title}</div>'
                f'<div class="mitup-inline-result__sub">{sub}</div></div></div>'
            )
    html = f'<div class="mitup-inline-results">{"".join(rows)}</div>'
    for old, new in rename:
        html = html.replace(old, new)
    return html


def annotated(name: str, notes: list[tuple[str, str, str]], header: str = HEADER, panel: bool = False, **kw) -> str:
    body = plant_notes(inline_panel(name, kw.get("rename", ())) if panel else bubble(name, **kw), notes)
    spans = "\n".join(
        f'  <span class="mitup-annotation mitup-annotation--{side}" data-for="{i}" style="top: 0px;">\n'
        f'    <span class="mitup-annotation__label">{label}</span>\n'
        '    <span class="mitup-annotation__line"></span>\n  </span>'
        for i, (_m, label, side) in enumerate(notes)
    )
    return f"""<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
{header}
    <div class="mitup-annotated__body">
      {body}
    </div>
  </div>
{spans}
</div>"""


def phone(name: str, **kw) -> str:
    return f"""<div class="mitup-phone">
  <div class="mitup-phone__screen">
    <div class="mitup-phone__status">
      <span>9:41</span>
      <div class="mitup-phone__notch"></div>
      <span class="mitup-phone__signal"><span>5G</span><span class="mitup-phone__battery"></span></span>
    </div>
{HEADER}
    <div class="mitup-phone__body">
      {bubble(name, **kw)}
    </div>
{INPUT_BAR}
  </div>
</div>"""


def place(page: Path, block_name: str, html: str):
    """Replace the block between <!-- mock:NAME --> and <!-- /mock --> in a markdown page."""
    text = page.read_text()
    start, end = f"<!-- mock:{block_name} -->", "<!-- /mock -->"
    a = text.index(start) + len(start)
    b = text.index(end, a)
    page.write_text(text[:a] + "\n" + html + "\n" + text[b:])


if __name__ == "__main__":
    print(annotated(sys.argv[1], []))
