from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderContext:
    """Cross-cutting user/session state shared by every view factory.

    Carries only the display concerns that would otherwise have to be threaded
    through every factory signature and call site — the acting user's language
    and whether they are an admin. Entity data (a meeting, ids, callback data,
    a message body) stays as explicit per-factory parameters and never belongs
    here.

    Built once per handler, typically via guards.render_context, and passed as
    the first positional argument to each factory.
    """

    lang: str
    is_admin: bool = False

    def with_lang(self, lang: str) -> RenderContext:
        """Return a copy that renders in *lang* instead of the acting user's language.

        Use this at the rare call sites that must render a screen in a language
        other than the acting user's — e.g. showing a meeting in the meeting's
        own language, or echoing back a language the user just picked. Everywhere
        else, pass the context through unchanged.
        """
        return dataclasses.replace(self, lang=lang)
