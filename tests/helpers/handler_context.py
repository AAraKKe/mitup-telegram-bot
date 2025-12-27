from dataclasses import dataclass

from telegram import Update

from tests.helpers.types import StubMitupApp


@dataclass
class HandlerContext:
    """
    Dataclass that contains the information needed to call a given handler from any test

    The intention is to be used as a fixture with the proper update and app so we do not need to call
    all fixtures needed to run a hnalder test: update and app.
    """

    update: Update
    app: StubMitupApp
