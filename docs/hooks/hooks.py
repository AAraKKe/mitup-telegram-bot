import logging
import os
from functools import reduce

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page

MITUP_BOT_LINK = os.environ.get("MITUP_BOT_LINK", "https://t.me/mitupbot")

log = logging.getLogger("mkdocs")


AUTOLINKS = {
    "@mitupbot": MITUP_BOT_LINK,
    "MitupBot": MITUP_BOT_LINK,
}


def insert_mitupbot_link(md: str, page: Page) -> str:
    def replace(acc: str, link: str) -> str:
        log.info(f"--- Replacing [{link}] -> {AUTOLINKS[link]}")
        return acc.replace(f"[{link}]", f"[{link}]({AUTOLINKS[link]})")

    return reduce(
        lambda acc, x: replace(acc, x),
        AUTOLINKS,
        md,
    )


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    log.info(f"- Running on_page_markdown for {page.file.src_uri}")
    return insert_mitupbot_link(markdown, page)
