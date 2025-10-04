from dataclasses import dataclass

from telegram import Update

from tests.helpers.types import StubMitupApp


@dataclass
class HandlerContext:
    """Dataclass that contains the information needed to call a given handler from any test"""

    update: Update
    app: StubMitupApp
