from mitup_bot.format_tags import strip_format_tags


def test_strips_tags_and_keeps_visible_text():
    assert strip_format_tags('<b>Hi</b> <a href="https://x.io">there</a>') == "Hi there"


def test_link_with_an_escaped_address_keeps_only_its_text():
    assert strip_format_tags('<a href="https://x.io/?a=1&amp;b=&quot;2&quot;&gt;">Meet</a> at 8') == "Meet at 8"


def test_tg_emoji_keeps_fallback_emoji():
    assert strip_format_tags('<tg-emoji emoji-id="42">😀</tg-emoji> hi') == "😀 hi"


def test_plain_text_unchanged():
    assert strip_format_tags("no tags here") == "no tags here"


def test_decodes_character_references():
    assert strip_format_tags("fish &amp; chips &#x27;n&#x27; &quot;dip&quot;") == "fish & chips 'n' \"dip\""


def test_tag_lookalike_literals_preserved():
    assert strip_format_tags("&lt;b&gt;hi&lt;/b&gt; and <3") == "<b>hi</b> and <3"


def test_placeholders_kept_verbatim():
    assert strip_format_tags("<b>hello</b> ${name} &amp; ${other}") == "hello ${name} & ${other}"
