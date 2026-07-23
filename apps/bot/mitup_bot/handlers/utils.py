from telegram import Update

from mitup_bot.mitup_types import TMitupContext
from mitup_bot.monitoring import Feature
from mitup_bot.utils import CommonMessages
from mitup_bot.views import MitupView, RenderContext


async def reply_rich_message_not_supported(
    ctx: RenderContext, update: Update, context: TMitupContext, view: MitupView
) -> None:
    """Reply to a rich message by re-prompting the current step.

    *view* is the step's own re-prompt view; the not-supported notice is prepended so the user
    keeps their buttons and reads the step as still open. The rich-message feature metric is
    recorded here so every per-step rich handler emits it exactly once.
    """
    view.with_context(CommonMessages.RICH_MESSAGE_NOT_SUPPORTED.get(lang=ctx.lang))
    await context.api.send_message(update=update, view=view)
    context.put_feature_metric(Feature.RICH_MESSAGE)
