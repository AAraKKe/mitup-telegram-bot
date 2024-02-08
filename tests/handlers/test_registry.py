from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes

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

    bindable_element = "CommandHandler[callback=test_only_bindable_handlers_are_registered.<locals>.command_bindable]"
    not_bindable_element = "CommandHandler[callback=test_only_bindable_handlers_are_registered.<locals>.command_not_bindable]"

    app = ApplicationBuilder().token("AAA").build()
    HandlersRegistry.bind(app)

    assert bindable_element in str(app.handlers)
    assert not_bindable_element not in str(app.handlers)
    assert "not_bindable_command" in HandlersRegistry.handlers
    assert "bindable_command" in HandlersRegistry.handlers
