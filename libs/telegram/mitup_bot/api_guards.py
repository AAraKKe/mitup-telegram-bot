"""Wire the Update-field guards into the telegram api's injected validator slot.

The api rendering layer (mitup_bot.api_wrapper) validates the chat or query on an incoming
Update before sending, but lives in the mitup-telegram library and must not depend on the
guards/handlers layer. It exposes an UpdateGuards slot instead; this module supplies the
concrete guards-backed validators and is registered at process startup, mirroring the db
reconciler wiring in mitup_bot.reconcile.
"""

import structlog

from mitup_bot import api_wrapper, guards

log = structlog.get_logger(__name__)


def register_update_guards():
    """Inject the guards-backed Update validators into the api; every process entry point that
    sends through the api calls this once at startup."""
    api_wrapper.set_update_guards(
        api_wrapper.UpdateGuards(
            chat=guards.chat,
            valid_inline_query=guards.valid_inline_query,
            valid_callback_query=guards.valid_callback_query,
        )
    )
    # The api asserts on this wiring far from here, when the first update is rendered; the
    # breadcrumb turns that assertion into a startup fact an operator can check for.
    log.info("Registered the update guards")
