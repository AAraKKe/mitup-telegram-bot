import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Hello, world"
    )


if __name__ == "__main__":
    application = ApplicationBuilder().token("xxxxxx").build()

    start_handler = CommandHandler("start", start)
    application.add_handler(start_handler)

    application.run_polling()
