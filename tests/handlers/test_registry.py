from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from mitup_bot.handlers import HandlersRegistry


def test_registry_has_handlers():
    assert len(HandlersRegistry.handlers) > 0


def test_handlers_registered_when_bound_to_app():
    # Given some application
    app = ApplicationBuilder().token("AAA").build()

    # With no handlers to begin with
    assert len(app.handlers) == 0

    # When we bind it with the registry
    HandlersRegistry.bind(app)

    # The app now has those handlers
    assert len(app.handlers) > 0


def test_only_bindable_handlers_are_registered():
    @HandlersRegistry.register_command("not_bindable_command", bindable=False)
    async def command_not_bindable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    @HandlersRegistry.register_command("bindable_command", bindable=True)
    async def command_bindable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return "Done!"

    app = ApplicationBuilder().token("AAA").build()
    HandlersRegistry.bind(app)
    command_handlers = [
        next(iter(handler.commands))
        for handler_list in app.handlers.values()
        for handler in handler_list
        if isinstance(handler, CommandHandler)
    ]

    assert "bindable" in command_handlers
    assert "not_bindable" not in command_handlers
    assert "not_bindable_command" in HandlersRegistry.handlers
    assert "bindable_command" in HandlersRegistry.handlers
