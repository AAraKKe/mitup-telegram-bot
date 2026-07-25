import pytest
from structlog.testing import capture_logs
from telegram import Chat, Update
from telegram.error import TelegramError

from mitup_bot.config import Env
from mitup_bot.custom_context import BOT_CONFIG_KEY, fault_fields_from_update
from mitup_bot.exceptions import (
    CallbackQueryNotSet,
    ContextPropertyNotSetError,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    EffectiveUserNotSet,
    GuardError,
    InactiveUserInteraction,
    InlineQueryNotSetError,
    MalformedCallbackData,
    MeetingAccessError,
    MeetingGoneError,
    MeetingInactiveOwnerError,
    MeetingNotOwnedError,
    SharedMeetingDeniedError,
    SharedMeetingError,
    SharedMeetingFinishedError,
    SharedMeetingGoneError,
    UserNotFound,
    UserPendingDeletion,
)
from mitup_bot.handlers import error_handler
from mitup_bot.handlers.error_handler import SUPPRESSED_EXCEPTIONS
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.registry import callback_with_metrics
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    CommonMessages,
    MeetingDisplayMessages,
    MeetingInviteMessages,
    MessageBase,
    PrivacyMessages,
)
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    MockDbSession,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    build_context,
    create_bot_config,
    create_settings,
    create_user,
)
from tests.helpers.constants import DEFAULT_CHAT_ID, DEFAULT_USER_ID
from tests.helpers.fixtures import create_update
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize(
    "error, message",
    [(error, message) for error, messages in SUPPRESSED_EXCEPTIONS.items() for message in messages],
)
async def test_errors_ignored(error: type, message: str, context: StubMitupContext, metrics: MetricAssertions):
    error_obj = error(message)

    await error_handler.handler(context, error_obj, Env.DEV)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_not_found(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """When no user with that tg_user_id exists, handle_inactive_user returns silently without emitting metrics."""
    # Do not add any user to the session so the lookup returns None
    await error_handler.handle_inactive_user(context, tg_user_id=999)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_handle_inactive_user_error(
    context: StubMitupContext, user: User, mock_session: MockDbSession, metrics: MetricAssertions
):
    """MEMBER → LEFT transition: the metric MUST fire."""
    assert user.status is UserStatus.MEMBER

    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_joined_only_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """JOINED_ONLY users can never be DM-ed, so the error path must NOT transition them or emit the metric.

    Without this guard, every reminder send to a JOINED_ONLY user would mark them LEFT and
    eventually delete them via user_cleanup, defeating the entire purpose of the new enum.
    """
    user = create_user(id=10, tg_user_id=500, status=UserStatus.JOINED_ONLY)
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.JOINED_ONLY
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_left_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Re-hitting an already-LEFT user must not double-emit the INACTIVE_USER_SET metric."""
    user = create_user(id=11, tg_user_id=501, status=UserStatus.LEFT)
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, InactiveUserInteraction(user.tg_user_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


# --- Pending deletion handling ---


async def test_pending_deletion_answers_callback_query_with_alert(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A callback query from a marked user is answered with the alert and nothing else happens."""
    context = build_callback_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "es"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_answer_callback_query_called(
        context.telegram_update, text=PrivacyMessages.PENDING_DELETION_ALERT.get_text(lang="es"), show_alert=True
    )
    context.api.assert_send_message_not_called()
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_replies_to_message_updates(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A message update has no callback query to answer, so the alert is sent as a plain message."""
    context = build_message_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_send_message_called(
        context.telegram_update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang="en")
    )
    context.api.assert_method_just_called("answer_callback_query", times=0)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_answers_inline_queries_with_no_results(
    app: StubMitupApp, mock_session: MockDbSession, metrics: MetricAssertions
):
    """An inline query from a marked user is answered empty so the client spinner clears."""
    context = build_inline_context(app)

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)
    await context.metrics.flush()

    context.api.assert_answer_inline_query_called(context.telegram_update, results=[], cache_time=0)
    context.api.assert_send_message_not_called()
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_pending_deletion_does_not_change_user_status(
    context: StubMitupContext, user: User, mock_session: MockDbSession
):
    """Unlike InactiveUserInteraction, the pending-deletion branch must not touch the user row:
    the DELETION_REQUESTED status is what the cleanup run keys on."""
    user.status = UserStatus.DELETION_REQUESTED
    mock_session.add_object(user, query_field="tg_user_id")

    await error_handler.handler(context, UserPendingDeletion(user.tg_user_id, "en"), Env.PROD)

    assert user.status is UserStatus.DELETION_REQUESTED


async def test_pending_deletion_suppresses_delivery_failures(app: StubMitupApp, mock_session: MockDbSession):
    """Delivery is best-effort: a failing answer must not escape as a second fault."""
    context = build_callback_context(app)
    context.api.mock_method("answer_callback_query").side_effect = TelegramError("answer failed")

    # Must not raise a second exception.
    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)


async def test_pending_deletion_returns_early_when_no_update(app: StubMitupApp, mock_session: MockDbSession):
    """When the context has no telegram update, the branch is a no-op (no send, no raise)."""
    context = build_message_context(app)
    # telegram_update is typed Update, but production reads it as Update | None and short-circuits on
    # None. Forcing None here is the only way to exercise that branch; it is an intentional test-only
    # violation, not a ty false positive, so it is exempted from requiring a tracking issue.
    context.telegram_update = None  # ty: ignore[invalid-assignment]  # nolink: intentional — exercising the None short-circuit branch

    await error_handler.handler(context, UserPendingDeletion(123, "en"), Env.PROD)

    context.api.assert_method_just_called("send_message", times=0)
    context.api.assert_method_just_called("answer_callback_query", times=0)


# --- Meeting guard rejections ---

BACK_ROWS: Keyboard = [[ButtonConfig(text="Back to the list", callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))]]

# The one flow that opts into a context sentence today; any MessageBase member would do here.
INVITE_FLOW_CONTEXT = MeetingInviteMessages.FLOW_CONTEXT


def meeting_rejections() -> list[tuple[MeetingAccessError, MitupView]]:
    """Every rejection `guards.meeting` raises, paired with the screen that answers it."""
    ctx = RenderContext(lang="en")
    return [
        (
            MeetingGoneError(meeting_id=7, action="Edit title", user_db_id=1, lang="en", keyboard=BACK_ROWS),
            factory.deleted_meeting_view(ctx, back_rows=BACK_ROWS),
        ),
        (
            MeetingGoneError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"),
            factory.deleted_meeting_view(ctx),
        ),
        (
            MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"),
            factory.main_menu_view(ctx),
        ),
        (
            MeetingInactiveOwnerError(meeting_id=7, action="Edit title", user_db_id=1, lang="en", keyboard=BACK_ROWS),
            factory.reactivation_prompt_view(ctx, meeting_id=7, back_rows=BACK_ROWS),
        ),
        (
            MeetingInactiveOwnerError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"),
            factory.reactivation_prompt_view(ctx, meeting_id=7),
        ),
    ]


REJECTION_PARAMS = [
    pytest.param(error, view, id=f"{type(error).__name__}{'_with_back_rows' if error.keyboard else ''}")
    for error, view in meeting_rejections()
]


@pytest.mark.parametrize("error, expected_view", REJECTION_PARAMS)
async def test_meeting_rejection_edits_the_message_for_callback_queries(
    app: StubMitupApp, mock_session: MockDbSession, error: MeetingAccessError, expected_view: MitupView
):
    """A tapped button is answered in place, so the rejection replaces the screen it came from."""
    context = build_callback_context(app)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_edit_message_called(context.telegram_update, expected_view)
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize("error, expected_view", REJECTION_PARAMS)
async def test_meeting_rejection_replies_to_message_updates(
    app: StubMitupApp, mock_session: MockDbSession, error: MeetingAccessError, expected_view: MitupView
):
    """A message update has no message of ours to replace, so the rejection is a fresh reply."""
    context = build_message_context(app)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_send_message_called(context.telegram_update, expected_view)
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("error, expected_view", REJECTION_PARAMS)
async def test_meeting_rejection_answers_inline_queries_with_the_unavailable_card(
    app: StubMitupApp, mock_session: MockDbSession, error: MeetingAccessError, expected_view: MitupView
):
    """An inline query can only carry results, so every rejection becomes the unavailable card."""
    context = build_inline_context(app)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_answer_inline_query_called(
        context.telegram_update, results=[meeting_views.unavailable_inline_view("en")], cache_time=0
    )
    context.api.assert_send_message_not_called()
    context.api.assert_edit_message_not_called()


async def test_meeting_rejection_renders_in_the_language_the_error_carries(
    app: StubMitupApp, mock_session: MockDbSession
):
    """The guard puts the acting user's language on the exception, sparing the renderer a DB round-trip."""
    context = build_callback_context(app)

    await error_handler.handler(
        context, MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="es"), Env.PROD
    )

    context.api.assert_edit_message_called(context.telegram_update, factory.main_menu_view(RenderContext(lang="es")))


async def test_meeting_rejection_keeps_the_admin_row_for_admins(app: StubMitupApp, mock_session: MockDbSession):
    """The redirect is built for the acting user, so an admin still sees the Admin row on it."""
    context = build_callback_context(app)
    context.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])

    await error_handler.handler(
        context, MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"), Env.PROD
    )

    context.api.assert_edit_message_called(
        context.telegram_update, factory.main_menu_view(RenderContext(lang="en", is_admin=True))
    )


@pytest.mark.parametrize("error, expected_view", REJECTION_PARAMS)
async def test_meeting_rejection_closes_the_interaction_without_a_fault(
    context: StubMitupContext,
    mock_session: MockDbSession,
    metrics: MetricAssertions,
    error: MeetingAccessError,
    expected_view: MitupView,
):
    """A stale button is an expected outcome: the interaction ends on FAULT=0, like a completed handler."""
    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_meeting_not_owned_is_counted_and_logged(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The ownership rejection keeps its own counter and its warning line, both emitted here."""
    error = MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=1)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] == [str(error)]


async def test_meeting_gone_is_logged_without_touching_the_ownership_counter(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    error = MeetingGoneError(meeting_id=7, action="Edit title", user_db_id=1, lang="en")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=1)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] == [str(error)]


def rejection_screens(flow_context: MessageBase | None) -> list[tuple[MeetingAccessError, MitupView]]:
    """Each bot-chat rejection, paired with the screen it stands for before any sentence is appended."""
    ctx = RenderContext(lang="en")
    return [
        (
            MeetingGoneError(meeting_id=7, action="Edit title", user_db_id=1, lang="en", flow_context=flow_context),
            factory.deleted_meeting_view(ctx),
        ),
        (
            MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en", flow_context=flow_context),
            factory.main_menu_view(ctx),
        ),
        (
            MeetingInactiveOwnerError(
                meeting_id=7, action="Edit title", user_db_id=1, lang="en", flow_context=flow_context
            ),
            factory.reactivation_prompt_view(ctx, meeting_id=7),
        ),
    ]


FLOW_CONTEXT_PARAMS = [
    pytest.param(error, view, id=type(error).__name__) for error, view in rejection_screens(INVITE_FLOW_CONTEXT)
]

PLAIN_SCREEN_PARAMS = [pytest.param(error, view, id=type(error).__name__) for error, view in rejection_screens(None)]


@pytest.mark.parametrize("error, plain_view", FLOW_CONTEXT_PARAMS)
async def test_flow_context_adds_one_sentence_to_the_same_screen(
    app: StubMitupApp, mock_session: MockDbSession, error: MeetingAccessError, plain_view: MitupView
):
    """The flow sentence is appended to the screen the rejection already stands for: one reply, one edit."""
    context = build_callback_context(app)

    await error_handler.handler(context, error, Env.PROD)

    delivered: MitupView = context.api.call_args("edit_message").kwargs["view"]
    assert delivered.description.text == (f"{plain_view.description.text}\n\n{INVITE_FLOW_CONTEXT.get_text(lang='en')}")
    # The screen itself is untouched: same buttons, and the formatting of the original copy survives.
    assert delivered.keyboard == plain_view.keyboard
    assert delivered.description.entities == plain_view.description.entities
    context.api.assert_method_just_called("edit_message", times=1)
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize("error, plain_view", PLAIN_SCREEN_PARAMS)
async def test_rejection_without_a_flow_context_renders_the_screen_unchanged(
    app: StubMitupApp, mock_session: MockDbSession, error: MeetingAccessError, plain_view: MitupView
):
    """A call site that opts out gets the screen its rejection stands for, description untouched."""
    context = build_callback_context(app)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_edit_message_called(context.telegram_update, plain_view)


async def test_flow_context_renders_in_the_language_the_rejection_carries(
    app: StubMitupApp, mock_session: MockDbSession
):
    """The sentence is part of the screen, so it follows the screen's language, not the default."""
    context = build_callback_context(app)
    error = MeetingGoneError(
        meeting_id=7, action="invite users to a meeting", user_db_id=1, lang="es", flow_context=INVITE_FLOW_CONTEXT
    )

    await error_handler.handler(context, error, Env.PROD)

    delivered: MitupView = context.api.call_args("edit_message").kwargs["view"]
    assert delivered.description.text.endswith(INVITE_FLOW_CONTEXT.get_text(lang="es"))
    assert delivered.keyboard == factory.deleted_meeting_view(RenderContext(lang="es")).keyboard


async def test_flow_context_is_not_added_to_the_unavailable_inline_card(app: StubMitupApp, mock_session: MockDbSession):
    """An inline query carries results, not a screen: it is answered with the card and nothing else."""
    context = build_inline_context(app)
    error = MeetingGoneError(
        meeting_id=7, action="invite users to a meeting", user_db_id=1, lang="en", flow_context=INVITE_FLOW_CONTEXT
    )

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_answer_inline_query_called(
        context.telegram_update, results=[meeting_views.unavailable_inline_view("en")], cache_time=0
    )
    context.api.assert_edit_message_not_called()


async def test_meeting_inactive_owner_is_a_plain_screen(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Offering an owner the reactivation prompt says nothing about their intent: no warning, no counter."""
    error = MeetingInactiveOwnerError(meeting_id=7, action="Edit title", user_db_id=1, lang="en")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.ERROR.with_prefix(MetricKey.MEETING_NOT_OWNED), value=1)
    assert [entry for entry in logs if entry["log_level"] == "warning"] == []


# --- Shared-surface rejections ---


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_shared_meeting_gone_replaces_the_card_and_counts_it_stale(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A card that outlived its meeting is replaced by the deleted banner, on its own counter."""
    error = SharedMeetingGoneError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="en")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    context.api.assert_edit_message_called(
        context.telegram_update,
        MitupView(
            description=MeetingDisplayMessages.DELETED_BANNER.get(lang="en"),
            keyboard=factory.main_menu_back_rows("en"),
        ),
    )
    metrics.assert_emitted(name=MetricKey.STALE_MEETING_MESSAGE, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] == []


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_shared_meeting_finished_replaces_the_card_with_the_finished_banner(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The banner replaces the card; in the bot's chat it keeps the way back to the main menu."""
    error = SharedMeetingFinishedError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="es")

    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    context.api.assert_edit_message_called(
        context.telegram_update,
        MitupView(
            description=MeetingDisplayMessages.FINISHED_BANNER.get(lang="es"),
            keyboard=factory.main_menu_back_rows("es"),
        ),
    )
    metrics.assert_not_emitted(name=MetricKey.STALE_MEETING_MESSAGE, value=1)
    metrics.assert_not_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)


SHARED_BANNERS: list[tuple[SharedMeetingError, MeetingDisplayMessages]] = [
    (
        SharedMeetingGoneError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="en"),
        MeetingDisplayMessages.DELETED_BANNER,
    ),
    (
        SharedMeetingFinishedError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="en"),
        MeetingDisplayMessages.FINISHED_BANNER,
    ),
]

SHARED_BANNER_PARAMS = [pytest.param(error, banner, id=type(error).__name__) for error, banner in SHARED_BANNERS]


@pytest.mark.parametrize("error, banner", SHARED_BANNER_PARAMS)
@pytest.mark.parametrize("chat_type", [Chat.GROUP, Chat.SUPERGROUP, Chat.CHANNEL])
async def test_shared_banner_carries_no_keyboard_outside_the_bot_chat(
    app: StubMitupApp,
    mock_session: MockDbSession,
    error: SharedMeetingError,
    banner: MeetingDisplayMessages,
    chat_type: str,
):
    """A card in a conversation between people is replaced in place: the banner offers no navigation."""
    context = build_card_context(app, chat_type)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_edit_message_called(
        context.telegram_update, MitupView(description=banner.get(lang="en"), keyboard=[])
    )


@pytest.mark.parametrize("error, banner", SHARED_BANNER_PARAMS)
async def test_shared_banner_offers_the_main_menu_in_the_bot_chat(
    app: StubMitupApp, mock_session: MockDbSession, error: SharedMeetingError, banner: MeetingDisplayMessages
):
    """In the bot's own chat the banner is the whole screen, so it leads somewhere."""
    context = build_card_context(app, Chat.PRIVATE)

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_edit_message_called(
        context.telegram_update,
        MitupView(description=banner.get(lang="en"), keyboard=factory.main_menu_back_rows("en")),
    )


@pytest.mark.parametrize("error, banner", SHARED_BANNER_PARAMS)
async def test_shared_banner_carries_no_keyboard_on_an_inline_card(
    app: StubMitupApp, mock_session: MockDbSession, error: SharedMeetingError, banner: MeetingDisplayMessages
):
    """A card shared as an inline message reports no chat at all, and can sit in any of them."""
    context = build_inline_card_context(app)
    assert context.telegram_update.effective_chat is None

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_edit_message_called(
        context.telegram_update, MitupView(description=banner.get(lang="en"), keyboard=[])
    )


async def test_shared_meeting_denied_keeps_the_card_untouched_in_the_bot_chat(
    app: StubMitupApp, mock_session: MockDbSession
):
    """The denial is an alert over an untouched card everywhere, the bot's own chat included."""
    context = build_card_context(app, Chat.PRIVATE)
    error = SharedMeetingDeniedError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="en")

    await error_handler.handler(context, error, Env.PROD)

    context.api.assert_answer_callback_query_called(
        context.telegram_update, text=MeetingDisplayMessages.DELETED_BANNER.get(lang="en"), show_alert=True
    )
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=True)], indirect=True)
async def test_shared_meeting_denied_alerts_over_the_card_without_touching_it(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A denial leaves the card alone and says nothing about the meeting beyond the deleted copy."""
    error = SharedMeetingDeniedError(meeting_id=7, action="join or leave a meeting", user_db_id=1, lang="en")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    context.api.assert_answer_callback_query_called(
        context.telegram_update, text=MeetingDisplayMessages.DELETED_BANNER.get(lang="en"), show_alert=True
    )
    context.api.assert_edit_message_not_called()
    metrics.assert_emitted(name=MetricKey.UNAUTHORIZED_MEETING_CALLBACK, value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [entry["event"] for entry in warnings] == [str(error)]


async def test_meeting_rejection_suppresses_delivery_failures(app: StubMitupApp, mock_session: MockDbSession):
    """Delivery is best-effort: a failing edit must not escape as a second, unhandled fault."""
    context = build_callback_context(app)
    context.api.mock_method("edit_message").side_effect = TelegramError("edit failed")

    # Must not raise a second exception.
    await error_handler.handler(
        context, MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"), Env.PROD
    )


async def test_meeting_rejection_returns_early_when_no_update(app: StubMitupApp, mock_session: MockDbSession):
    """With no telegram update there is nothing to answer, and the branch is a no-op."""
    context = build_message_context(app)
    # telegram_update is typed Update, but production reads it as Update | None and short-circuits on
    # None. Forcing None here is the only way to exercise that branch; it is an intentional test-only
    # violation, not a ty false positive, so it is exempted from requiring a tracking issue.
    context.telegram_update = None  # ty: ignore[invalid-assignment]  # nolink: intentional — exercising the None short-circuit branch

    await error_handler.handler(
        context, MeetingNotOwnedError(meeting_id=7, action="Edit title", user_db_id=1, lang="en"), Env.PROD
    )

    context.api.assert_method_just_called("send_message", times=0)
    context.api.assert_edit_message_not_called()


async def test_handle_error_for_uncaght_exception(context: StubMitupContext, metrics: MetricAssertions):
    context.prepare_handler_metrics({"Handler": "SomeHandler", "HandlerType": "Callback"})

    try:
        # We need to raise the exception to have exec_info available when the error is handled
        raise RuntimeError()
    except RuntimeError:
        await error_handler.handler(context, RuntimeError(), Env.DEV)
        await context.metrics.flush()

    # The dimensionless aggregate FAULT is emitted exactly once (the infra alarms read it),
    # carrying the trigger context and the exception class so the CloudWatch fault record is
    # self-contained. The class is a property: a metric name minted per exception class would be a
    # new billed series nothing charts.
    metrics.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        times=1,
        dimensions={},
        dimensions_exact=True,
        properties={
            "UpdatePayload": fault_fields_from_update(context.telegram_update),
            "error_type": "builtins.RuntimeError",
            "Handler": "SomeHandler",
            "HandlerType": "Callback",
        },
    )
    metrics.assert_not_emitted(name=MetricKey.FAULT.with_prefix("RuntimeError"))


async def test_handle_error_logs_the_exception(context: StubMitupContext):
    """Every fault produces a structured error line with the traceback attached, in every env —
    the log-side record that carries the handler contextvars the EMF Fault record lacks."""
    context.prepare_handler_metrics({"Handler": "SomeHandler", "HandlerType": "Callback"})
    error = ValueError("boom")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)

    fault_logs = [entry for entry in logs if entry["event"] == "An error occurred while handling the update"]
    assert len(fault_logs) == 1
    assert fault_logs[0]["log_level"] == "error"
    assert fault_logs[0]["exc_info"] is error
    # The failure log carries the trigger and its context so a fault is debuggable from the log
    # alone (what the user sent or pressed, and who/where).
    assert fault_logs[0]["update"] == fault_fields_from_update(context.telegram_update)
    assert "trigger_text" in fault_logs[0]["update"] or "callback_data" in fault_logs[0]["update"]


# --- Guard error handling ---

# Every guard exception must keep subclassing GuardError (so the error handler classifies it) and
# RuntimeError (so existing `except RuntimeError` behaviour is preserved). Reparenting these is the
# whole point of the feature; this guards against an accidental base change.
ALL_GUARD_ERRORS = [
    EffectiveUserNotSet,
    UserNotFound,
    EffectiveChatNotSet,
    EffectiveMessageNotSet,
    CallbackQueryNotSet,
    MalformedCallbackData,
    InlineQueryNotSetError,
    MeetingAccessError,
    MeetingGoneError,
    MeetingNotOwnedError,
    MeetingInactiveOwnerError,
]


@pytest.mark.parametrize("guard_error", ALL_GUARD_ERRORS)
def test_guard_errors_subclass_guard_error_and_runtime_error(guard_error: type[Exception]):
    assert issubclass(guard_error, GuardError)
    assert issubclass(guard_error, RuntimeError)


def build_callback_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(callback_query=True))
    return build_context(update, app)


def build_card_context(app: StubMitupApp, chat_type: str) -> StubMitupContext:
    """A tap on a meeting card sitting in a chat of `chat_type`."""
    update = create_update(UpdateRequest(callback_query=True), tg_chat=Chat(id=DEFAULT_CHAT_ID, type=chat_type))
    return build_context(update, app)


def build_inline_card_context(app: StubMitupApp) -> StubMitupContext:
    """A tap on a card shared through inline mode: Telegram sends its id, never the chat it sits in."""
    update = create_update(UpdateRequest(callback_query=cb.JOIN.with_id(7), from_bot_chat=False))
    return build_context(update, app)


def build_message_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(message=True))
    return build_context(update, app)


def build_inline_context(app: StubMitupApp) -> StubMitupContext:
    update = create_update(UpdateRequest(message=False, inline_query="123"))
    return build_context(update, app)


async def test_guard_error_emits_fault_metrics_and_notifies_user(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A GuardError must emit the single aggregate FAULT naming its class and redirect the user."""
    # No user registered -> resolve_lang falls back to the project default language.
    error = EffectiveMessageNotSet(context.telegram_update)

    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(
        name=MetricKey.FAULT,
        value=1,
        times=1,
        properties={"error_type": f"{EffectiveMessageNotSet.__module__}.EffectiveMessageNotSet"},
    )
    metrics.assert_not_emitted(name=MetricKey.FAULT.with_prefix("EffectiveMessageNotSet"))

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_guard_error_uses_resolved_user_language(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """When the effective user exists in the DB, the notification uses that user's language."""
    # The default update fixture carries tg_user_id=123; register a matching user with a Spanish setting.
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    await error_handler.handler(context, EffectiveMessageNotSet(context.telegram_update), Env.PROD)
    await context.metrics.flush()

    expected_view = factory.main_menu_view(
        RenderContext(lang="es"), message=CommonMessages.UNEXPECTED_ERROR.get(lang="es")
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_non_guard_error_emits_fault_and_notifies(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A plain (non-guard) error still emits the full fault metrics AND redirects the user to the
    main menu with the generic notice, since any fault otherwise strands them mid-action."""
    # No user registered -> resolve_lang falls back to the project default language.
    await error_handler.handler(context, ValueError("boom"), Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1, properties={"error_type": "builtins.ValueError"})
    metrics.assert_not_emitted(name=MetricKey.FAULT.with_prefix("ValueError"))

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_fault_notification_failure_does_not_raise(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The post-fault user notification is best-effort: a delivery failure is swallowed, not re-raised
    as a second fault, while the original fault metrics still land."""
    context.api.mock_method("send_message").side_effect = TelegramError("send failed")

    # Must not raise despite the failing delivery.
    await error_handler.handler(context, ValueError("boom"), Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1, properties={"error_type": "builtins.ValueError"})


# --- Context loss handling ---


async def test_context_lost_notifies_user_and_emits_dedicated_metric(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """A lost-context error is an expected state, not a fault: the user is redirected to the main menu
    with the context-lost note, the dedicated CONTEXT_LOST metric fires, and no FAULT is emitted."""
    # No user registered -> resolve_lang falls back to the project default language.
    error = ContextPropertyNotSetError("User data 'meeting_id' requested but not set")

    await error_handler.handler(context, error, Env.PROD)
    await context.metrics.flush()

    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)

    fallback = TranslationEngine.FALLBACK_LANG
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.CONTEXT_LOST.get(lang=fallback)
    )
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_context_lost_uses_resolved_user_language(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """The context-lost note renders in the effective user's language when they exist in the DB."""
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    await error_handler.handler(
        context, ContextPropertyNotSetError("User data 'meeting_id' requested but not set"), Env.PROD
    )
    await context.metrics.flush()

    expected_view = factory.main_menu_view(RenderContext(lang="es"), message=CommonMessages.CONTEXT_LOST.get(lang="es"))
    context.api.assert_send_message_called(context.telegram_update, expected_view)


async def test_context_lost_logs_at_warning_without_fault_line(context: StubMitupContext):
    """Context loss is logged as a warning and never produces the generic fault log line."""
    error = ContextPropertyNotSetError("User data 'meeting_id' requested but not set")

    with capture_logs() as logs:
        await error_handler.handler(context, error, Env.PROD)

    event = "Conversation context was lost while handling the update"
    warning_logs = [entry for entry in logs if entry["event"] == event]
    assert len(warning_logs) == 1
    assert warning_logs[0]["log_level"] == "warning"
    assert warning_logs[0]["exc_info"] is error
    assert not [entry for entry in logs if entry["event"] == "An error occurred while handling the update"]


async def test_resolve_lang_returns_user_lang_when_user_exists(update: Update, mock_session: MockDbSession):
    user = create_user(id=1, tg_user_id=123, settings=create_settings(language="es"))
    mock_session.add_user(user)

    lang = await error_handler.resolve_lang(update)

    assert lang == "es"


async def test_resolve_lang_falls_back_when_update_is_none(mock_session: MockDbSession):
    lang = await error_handler.resolve_lang(None)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_resolve_lang_falls_back_when_no_effective_user(mock_session: MockDbSession):
    # An empty update has no effective_user.
    update = create_update(UpdateRequest(user=False, chat=False, message=False))

    lang = await error_handler.resolve_lang(update)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_resolve_lang_falls_back_when_user_not_in_db(update: Update, mock_session: MockDbSession):
    # Do not register any user so the DB lookup returns None.
    lang = await error_handler.resolve_lang(update)

    assert lang == TranslationEngine.FALLBACK_LANG


async def test_notify_guard_error_callback_query_answers_then_sends(app: StubMitupApp, mock_session: MockDbSession):
    """A callback-query update is acknowledged with an empty answer before the fresh message is sent."""
    context = build_callback_context(app)

    await error_handler.notify_guard_error(context)

    update = context.telegram_update
    # No user registered -> the notification falls back to the project default language.
    fallback = TranslationEngine.FALLBACK_LANG
    context.api.assert_answer_callback_query_called(update, text="", show_alert=False)
    expected_view = factory.main_menu_view(
        RenderContext(lang=fallback), message=CommonMessages.UNEXPECTED_ERROR.get(lang=fallback)
    )
    context.api.assert_send_message_called(update, expected_view)


async def test_notify_guard_error_message_update_only_sends(app: StubMitupApp, mock_session: MockDbSession):
    """A plain message update has no callback query to answer, only the message is sent."""
    context = build_message_context(app)

    await error_handler.notify_guard_error(context)

    context.api.assert_method_just_called("answer_callback_query", times=0)
    context.api.assert_method_just_called("send_message", times=1)


async def test_notify_guard_error_returns_early_when_no_update(app: StubMitupApp, mock_session: MockDbSession):
    """When the context has no telegram update, notify_guard_error is a no-op (no send, no raise)."""
    context = build_message_context(app)
    # telegram_update is typed Update, but production reads it as Update | None and short-circuits on
    # None. Forcing None here is the only way to exercise that branch; it is an intentional test-only
    # violation, not a ty false positive, so it is exempted from requiring a tracking issue.
    context.telegram_update = None  # ty: ignore[invalid-assignment]  # nolink: intentional — exercising the None short-circuit branch

    await error_handler.notify_guard_error(context)

    context.api.assert_method_just_called("send_message", times=0)
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "raised",
    [
        TelegramError("send failed"),
        EffectiveChatNotSet(create_update(UpdateRequest(message=True))),
        RuntimeError("unexpected internal failure"),
    ],
)
async def test_notify_guard_error_suppresses_send_failures(
    app: StubMitupApp, mock_session: MockDbSession, raised: Exception
):
    """notify_guard_error swallows ALL exceptions during delivery, not just TelegramError/GuardError."""
    context = build_message_context(app)
    context.api.mock_method("send_message").side_effect = raised

    # Must not raise a second exception.
    await error_handler.notify_guard_error(context)


@pytest.mark.parametrize(
    "raised",
    [
        TelegramError("callback ack failed"),
        RuntimeError("unexpected internal failure in callback ack"),
    ],
)
async def test_notify_guard_error_suppresses_callback_ack_failures(
    app: StubMitupApp, mock_session: MockDbSession, raised: Exception
):
    """notify_guard_error swallows exceptions raised while acknowledging a callback query."""
    context = build_callback_context(app)
    context.api.mock_method("answer_callback_query").side_effect = raised

    # Must not raise a second exception.
    await error_handler.notify_guard_error(context)


async def test_successful_callback_emits_fault_zero_without_notification(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Regression: the real callback_with_metrics success path emits global FAULT=0 and never notifies.

    This drives the actual registry wrapper (not a hand-emitted metric) with a non-raising callback,
    proving the guard-error notification branch is only reached on failure.
    """
    handler_was_called = False

    async def successful_callback(update: Update, ctx: StubMitupContext):
        nonlocal handler_was_called
        handler_was_called = True

    wrapped = callback_with_metrics(MainMenuHandlerId.MAIN_MENU_CALLBACK, "Callback", successful_callback, Env.PROD)
    await wrapped(context.telegram_update, context)

    assert handler_was_called
    # callback_with_metrics emits a single dimensionless FAULT=0 on the success path.
    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
    # The guard-error branch is never reached, so no notification is sent.
    context.api.assert_send_message_not_called()
    context.api.assert_method_just_called("answer_callback_query", times=0)
