import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest import mock

import pytest
import yaml
from telegram import Chat, Document, Location, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.error import BadRequest

from mitup_bot.handlers.broadcast import utils
from mitup_bot.handlers.broadcast.content import MAX_DOCUMENT_BYTES
from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId, ConversationBroadcastState
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Broadcast, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages, ButtonMessages
from mitup_bot.utils.rich_message import MAX_RICH_TEXT_LENGTH, RichContent, rich_text_length
from mitup_bot.views import MitupView
from tests.helpers import (
    HandlerContext,
    MockApi,
    MockDbSession,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_broadcast,
)
from tests.helpers.fixtures import create_update

if TYPE_CHECKING:
    from tests.helpers.types import RegisterAuthorDrafts, RegisterMember

ADMIN_TG_ID = 123
CHAT_ID = 123

# Plain unquoted YAML: no <, >, & or quotes, so `message.text_html` leaves it byte-identical and it
# round-trips to the same values. Formatting through the TEXT path must be authored with entities;
# literal tags must go through the DOCUMENT (raw bytes) path, which text_html never touches.
PLAIN_YAML = "- language: en\n  message: Hello\n- language: es_ES\n  message: Hola\n"
DOCUMENT_YAML = '- language: en\n  message: "<b>Hello</b>"\n- language: es_ES\n  message: "Hola"\n'


# --- Expected-view reconstruction (independent of the handler's own builder) ---
def expected_summary(
    lang: str,
    lines: list[tuple[str, int, int]],
    total: int,
    skipped: list[str],
) -> RichContent:
    parts = [BroadcastOperatorMessages.PREVIEW_SUMMARY_HEADER.rich(lang=lang)]
    parts.extend(
        BroadcastOperatorMessages.PREVIEW_SUMMARY_LINE.rich(
            lang=lang, language=language, char_count=char_count, recipient_count=recipient_count
        )
        for language, char_count, recipient_count in lines
    )
    parts.append(BroadcastOperatorMessages.PREVIEW_TOTAL_RECIPIENTS.rich(lang=lang, total=total))
    if skipped:
        parts.append(BroadcastOperatorMessages.PREVIEW_WARNINGS_HEADER.rich(lang=lang))
        parts.extend(
            BroadcastOperatorMessages.PREVIEW_WARNING_LINE.rich(lang=lang, language=language) for language in skipped
        )
    parts.append(BroadcastOperatorMessages.PREVIEW_FOOTER.rich(lang=lang))
    return RichContent.join("\n\n", parts)


def confirmation_keyboard(lang: str, broadcast_id: int) -> list[list[ButtonConfig]]:
    return [
        [
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CONFIRM.text(lang=lang),
                callback_data=cb.CONFIRM_BROADCAST.with_id(broadcast_id),
                style="success",
            ),
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CANCEL.text(lang=lang),
                callback_data=cb.CANCEL_BROADCAST.with_id(broadcast_id),
                style="danger",
            ),
        ]
    ]


def expected_language_label(lang: str, code: str) -> RichContent:
    display = utils.LANGUAGE_NAMES[code].rich(lang=lang)
    return BroadcastOperatorMessages.PREVIEW_LANGUAGE_LABEL.rich(lang=lang, language=display)


def recipient_keyboard(lang: str) -> list[list[ButtonConfig]]:
    """The single Main Menu button row every recipient gets, in that recipient's own language."""
    return [[ButtonConfig(text=ButtonMessages.MAIN_MENU.text(lang=lang), callback_data=cb.SEND_MAIN_MENU)]]


def assert_previews(context: StubMitupContext, operator: User, lang: str, previews: list[tuple[str, str]]):
    """Previews are a header, then per language a bold label followed by the recipient's own view.

    Each preview carries the authored body verbatim and the recipient's own Main Menu button row (in
    the preview's language), so the operator sees exactly what will be delivered.
    """
    calls = context.api.call_args_list("send_message_to_user")
    expected_views: list[RichContent | MitupView] = [BroadcastOperatorMessages.PREVIEW_HEADER.rich(lang=lang)]
    for code, body in previews:
        expected_views.append(expected_language_label(lang, code))
        expected_views.append(MitupView(RichContent.from_markup(body), recipient_keyboard(code)))
    assert len(calls) == len(expected_views)
    for call, view in zip(calls, expected_views, strict=True):
        assert call.kwargs["user"] is operator
        assert call.kwargs["view"] == view


def rendered_preview(context: StubMitupContext) -> RichContent:
    """The rendered body of the last preview message for the single language under test."""
    return context.api.call_args_list("send_message_to_user")[-1].kwargs["view"].message


def added_broadcast(mock_session: MockDbSession) -> Broadcast:
    broadcasts = [obj for obj in mock_session.objects_added if isinstance(obj, Broadcast)]
    assert len(broadcasts) == 1, f"Expected exactly one persisted draft, got {len(broadcasts)}"
    return broadcasts[0]


def patch_member_counts(counts: dict[str, int]) -> mock._patch:
    return mock.patch.object(utils, "count_members_by_language", new=mock.AsyncMock(return_value=counts))


def api_refusing_the_english_preview(error: BadRequest) -> MockApi:
    """An api whose third message to the operator fails: the header and the English label land, the
    English preview does not."""
    api = MockApi()
    api.register_on_method("send_message_to_user", side_effect=[None, None, error])
    return api


def rendered_len(body: str) -> int:
    """The visible length the summary reports, which is the body with its tags taken off."""
    return rich_text_length(body)


# --- Document helpers ---
def document_update(app: StubMitupApp, tg_user: TgUser, *, file_size: int | None) -> Update:
    document = Document(file_id="file-id", file_unique_id="unique-id", file_size=file_size)
    message = Message(
        message_id=1,
        date=dt.datetime(2023, 1, 1, tzinfo=dt.UTC),
        chat=Chat(id=CHAT_ID, type="private"),
        from_user=tg_user,
        document=document,
    )
    return Update(1, message=message)


def set_downloaded_bytes(app: StubMitupApp, content: bytes):
    telegram_file = mock.MagicMock()
    telegram_file.download_as_bytearray = mock.AsyncMock(return_value=bytearray(content))
    app.bot.get_file = mock.AsyncMock(return_value=telegram_file)


def upload_document(handler_context: HandlerContext, app: StubMitupApp, tg_user: TgUser, yaml_text: str) -> Update:
    """Author YAML the way a file upload does: raw bytes, so text_html escaping never applies."""
    raw = yaml_text.encode("utf-8")
    update = document_update(app, tg_user, file_size=len(raw))
    set_downloaded_bytes(app, raw)
    handler_context.update = update
    return update


# --- TEXT path: pasted plain YAML (no special chars) previews and summarizes ---
@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_valid_yaml_text_renders_preview(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    with patch_member_counts({"en": 5, "es_ES": 3}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    lang = user_with_settings.lang
    assert_previews(context, user_with_settings, lang, [("en", "Hello"), ("es_ES", "Hola")])
    draft = added_broadcast(mock_session)
    summary = expected_summary(
        lang,
        lines=[("en", rendered_len("Hello"), 5), ("es_ES", rendered_len("Hola"), 3)],
        total=8,
        skipped=[],
    )
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))


@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_preview_carries_the_recipient_main_menu_button(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    """Each preview carries the same single Main Menu button recipients get, in the preview's own
    language: plain label (no « back decoration) wired to SEND_MAIN_MENU."""
    register_member(user_with_settings)

    with patch_member_counts({"en": 1, "es_ES": 1}):
        context, _ = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    # Preview messages are the header, then (label, body) per language; the bodies are the views
    # carrying keyboards. Slice off the header and pick every second view starting at the first body.
    preview_bodies = context.api.call_args_list("send_message_to_user")[2::2]
    keyboards = [call.kwargs["view"].menu for call in preview_bodies]
    assert keyboards == [recipient_keyboard("en"), recipient_keyboard("es_ES")]
    # The Spanish preview's button label is the Spanish Main Menu string, not the operator's language.
    es_button = keyboards[1][0][0]
    assert es_button.callback_data == cb.SEND_MAIN_MENU
    assert es_button.text == ButtonMessages.MAIN_MENU.text(lang="es_ES")


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="- language: en\n  message: hi\n- language: zz\n  message: unknown\n")],
    indirect=True,
)
async def test_unknown_language_is_warned_and_flow_proceeds(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    with patch_member_counts({"en": 4}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    lang = user_with_settings.lang
    # Only the English message previews — the unknown language never becomes a message.
    assert_previews(context, user_with_settings, lang, [("en", "hi")])
    draft = added_broadcast(mock_session)
    summary = expected_summary(
        lang,
        lines=[("en", rendered_len("hi"), 4)],
        total=4,
        skipped=["zz"],
    )
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))


@pytest.mark.parametrize(
    "update",
    [UpdateRequest(message_text="- language: en\n  message: hi\n- language: de_DE\n  message: hallo\n")],
    indirect=True,
)
async def test_recipient_estimate_folds_unprovided_languages_into_english(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    """Members whose language has no message (here es_ES and it_IT) fall into the English bucket."""
    register_member(user_with_settings)

    with patch_member_counts({"en": 5, "de_DE": 2, "es_ES": 3, "it_IT": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    lang = user_with_settings.lang
    draft = added_broadcast(mock_session)
    assert_previews(context, user_with_settings, lang, [("en", "hi"), ("de_DE", "hallo")])
    # en = 5 own + 3 (es_ES) + 1 (it_IT) folded = 9; de_DE = 2; total = 11.
    summary = expected_summary(
        lang,
        lines=[("en", rendered_len("hi"), 9), ("de_DE", rendered_len("hallo"), 2)],
        total=11,
        skipped=[],
    )
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))
    assert state == ConversationBroadcastState.AWAITING_CONTENT


@pytest.mark.parametrize("update", [UpdateRequest(message_text=PLAIN_YAML)], indirect=True)
async def test_reupload_replaces_prior_draft(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    register_author_drafts: RegisterAuthorDrafts,
):
    register_member(user_with_settings)
    prior_draft = create_broadcast(id=77, name="old", author_tg_id=ADMIN_TG_ID)
    register_author_drafts(ADMIN_TG_ID, (prior_draft,))

    with patch_member_counts({"en": 1, "es_ES": 1}):
        await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    # The single-draft-per-author invariant deletes the old draft before persisting the new one.
    mock_session.assert_deleted(prior_draft)
    added_broadcast(mock_session)


# --- TEXT path: pasted formatting (Telegram entities) is serialized into tags via text_html ---
@pytest.mark.parametrize(
    "entity_type, url, expected_markup",
    [
        pytest.param(MessageEntity.BOLD, None, "<b>join</b>", id="bold"),
        pytest.param(
            MessageEntity.TEXT_LINK,
            "https://mitup.social",
            '<a href="https://mitup.social">join</a>',
            id="link",
        ),
    ],
)
async def test_pasted_formatting_entities_are_honored(
    entity_type: str,
    url: str | None,
    expected_markup: str,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    """A pasted message whose value carries a bold/link entity renders end-to-end with that entity:
    text_html serializes the entity into a tag, which the preview parses back into a FormattedText."""
    register_member(user_with_settings)
    yaml_text = "- language: en\n  message: join\n"
    offset = yaml_text.index("join")  # ASCII prefix, so char index == UTF-16 offset
    message_entity = MessageEntity(type=entity_type, offset=offset, length=len("join"), url=url)
    handler_context.update = create_update(UpdateRequest(message_text=yaml_text, entities=[message_entity]))

    with patch_member_counts({"en": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    preview = rendered_preview(context)
    assert preview.html == expected_markup
    added_broadcast(mock_session)


# --- Fatal validation errors (authored via the raw DOCUMENT path so YAML is preserved verbatim) ---
def too_long_yaml() -> str:
    return f'- language: en\n  message: "{"a" * (MAX_RICH_TEXT_LENGTH + 1)}"\n'


VALIDATION_CASES: list[tuple[str, str, Callable[[str], RichContent]]] = [
    (
        "not_a_list",
        "language: en\nmessage: hi\n",
        lambda lang: BroadcastOperatorMessages.ERROR_NOT_A_LIST.rich(lang=lang),
    ),
    (
        "empty_list",
        "[]\n",
        lambda lang: BroadcastOperatorMessages.ERROR_EMPTY_LIST.rich(lang=lang),
    ),
    (
        "entry_shape",
        '- language: en\n  message: "hi"\n  extra: nope\n',
        lambda lang: BroadcastOperatorMessages.ERROR_ENTRY_SHAPE.rich(lang=lang, position=1),
    ),
    (
        "entry_non_string",
        "- language: en\n  message: 5\n",
        lambda lang: BroadcastOperatorMessages.ERROR_ENTRY_SHAPE.rich(lang=lang, position=1),
    ),
    (
        "duplicate_language",
        '- language: en\n  message: "a"\n- language: en\n  message: "b"\n',
        lambda lang: BroadcastOperatorMessages.ERROR_DUPLICATE_LANGUAGE.rich(lang=lang, language="en"),
    ),
    (
        "missing_english",
        '- language: es_ES\n  message: "hola"\n',
        lambda lang: BroadcastOperatorMessages.ERROR_MISSING_ENGLISH.rich(lang=lang, language="en"),
    ),
    (
        "empty_message",
        '- language: en\n  message: "   "\n',
        lambda lang: BroadcastOperatorMessages.ERROR_EMPTY_MESSAGE.rich(lang=lang, language="en"),
    ),
    (
        "too_long",
        too_long_yaml(),
        lambda lang: BroadcastOperatorMessages.ERROR_MESSAGE_TOO_LONG.rich(
            lang=lang, language="en", length=MAX_RICH_TEXT_LENGTH + 1, limit=MAX_RICH_TEXT_LENGTH
        ),
    ),
]


@pytest.mark.parametrize(
    "yaml_text, expected_message",
    [pytest.param(yaml, builder, id=case_id) for case_id, yaml, builder in VALIDATION_CASES],
)
async def test_fatal_validation_error_keeps_operator_on_upload_step(
    mock_session: MockDbSession,
    yaml_text: str,
    expected_message: Callable[[str], RichContent],
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = upload_document(handler_context, app, tg_user, yaml_text)

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(update, expected_message(user_with_settings.lang))
    assert not any(isinstance(obj, Broadcast) for obj in mock_session.objects_added)


async def test_invalid_yaml_reports_parse_error(
    lang: str,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    upload_document(handler_context, app, tg_user, "::not yaml::\n: - [")

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    # The parser detail is dynamic, so assert the template's static prefix, in the operator's
    # language: hardcoding the English fragment would pass only while a catalog lacks the string.
    marker = "PARSER-DETAIL"
    template = BroadcastOperatorMessages.ERROR_INVALID_YAML.rich(lang=lang, detail=marker).text
    prefix = template.split(marker)[0].strip()
    sent_view = context.api.call_args("send_message").kwargs["view"]
    assert prefix in sent_view.text
    assert not any(isinstance(obj, Broadcast) for obj in mock_session.objects_added)


# --- The body is stored and previewed verbatim, and only Telegram judges its markup ---
@pytest.mark.parametrize(
    "body, visible_length",
    [
        pytest.param("<h2>Release 2.0</h2><ul><li>one</li><li>two</li></ul>", 20, id="heading_and_list"),
        pytest.param("<details><summary>More</summary>text</details>", 8, id="html_block"),
        pytest.param("<script>x</script>", 1, id="unknown_tag"),
        pytest.param("<b>x", 1, id="unbalanced_tag"),
    ],
)
async def test_authored_markup_reaches_the_preview_untouched(
    mock_session: MockDbSession,
    body: str,
    visible_length: int,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    """Nothing local rewrites or rejects markup: the body is previewed as written, and the summary
    counts what a reader sees, with the tags off. Telegram is the only judge of the markup."""
    register_member(user_with_settings)
    yaml_text = f"- language: en\n  message: {yaml.safe_dump(body)}"
    update = upload_document(handler_context, app, tg_user, yaml_text)

    with patch_member_counts({"en": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    assert_previews(context, user_with_settings, user_with_settings.lang, [("en", body)])
    draft = added_broadcast(mock_session)
    lang = user_with_settings.lang
    summary = expected_summary(lang, lines=[("en", visible_length, 1)], total=1, skipped=[])
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))


# --- The preview is the parse check: Telegram refusing one ends the submission ---
async def test_a_refused_preview_reports_the_language_and_reason_and_creates_no_draft(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = upload_document(handler_context, app, tg_user, DOCUMENT_YAML)
    refusal = BadRequest("Can't parse rich message: unclosed tag")

    with patch_member_counts({"en": 1, "es_ES": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE,
            handler_context=handler_context,
            api=api_refusing_the_english_preview(refusal),
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(
        update,
        BroadcastOperatorMessages.ERROR_PREVIEW_REJECTED.rich(
            lang=user_with_settings.lang, language="en", reason=refusal.message
        ),
    )
    assert not any(isinstance(obj, Broadcast) for obj in mock_session.objects_added)


async def test_a_refused_preview_stops_before_the_remaining_languages(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    """The English preview is the third message (header, label, body); a refusal there means the
    Spanish label and body never go out."""
    register_member(user_with_settings)
    upload_document(handler_context, app, tg_user, DOCUMENT_YAML)

    with patch_member_counts({"en": 1, "es_ES": 1}):
        context, _ = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE,
            handler_context=handler_context,
            api=api_refusing_the_english_preview(BadRequest("nope")),
        )

    assert len(context.api.call_args_list("send_message_to_user")) == 3


def tag_heavy_yaml() -> str:
    # 600 bold segments: rendered length 600 (under the cap) but ~4800 raw chars (over it), proving
    # the limit is measured on visible text, not markup.
    body = "<b>a</b>" * 600
    return f'- language: en\n  message: "{body}"\n'


async def test_tag_heavy_body_under_visible_limit_is_accepted(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = upload_document(handler_context, app, tg_user, tag_heavy_yaml())

    with patch_member_counts({"en": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    draft = added_broadcast(mock_session)
    lang = user_with_settings.lang
    # 600 visible chars — well under 4096 — even though the raw markup is far over it.
    summary = expected_summary(lang, lines=[("en", 600, 1)], total=1, skipped=[])
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))


# --- DOCUMENT path: literal-tag authoring (raw bytes, text_html never applies) ---
async def test_valid_yaml_via_document_renders_preview(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = upload_document(handler_context, app, tg_user, DOCUMENT_YAML)

    with patch_member_counts({"en": 2, "es_ES": 1}):
        context, state = await call_handler(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context
        )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    lang = user_with_settings.lang
    assert_previews(context, user_with_settings, lang, [("en", "<b>Hello</b>"), ("es_ES", "Hola")])
    draft = added_broadcast(mock_session)
    summary = expected_summary(
        lang,
        lines=[("en", rendered_len("<b>Hello</b>"), 2), ("es_ES", rendered_len("Hola"), 1)],
        total=3,
        skipped=[],
    )
    context.api.assert_send_message_called(update, MitupView(summary, confirmation_keyboard(lang, draft.db_id)))


async def test_document_rejected_when_declared_size_too_large(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = document_update(app, tg_user, file_size=MAX_DOCUMENT_BYTES + 1)
    app.bot.get_file = mock.AsyncMock()
    handler_context.update = update

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    # The declared size short-circuits before any download.
    app.bot.get_file.assert_not_awaited()
    context.api.assert_send_message_called(
        update,
        BroadcastOperatorMessages.ERROR_DOCUMENT_TOO_LARGE.rich(
            lang=user_with_settings.lang, limit_kb=MAX_DOCUMENT_BYTES // 1024
        ),
    )


async def test_document_rejected_when_decoded_size_too_large(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = document_update(app, tg_user, file_size=None)
    set_downloaded_bytes(app, b"a" * (MAX_DOCUMENT_BYTES + 1))
    handler_context.update = update

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(
        update,
        BroadcastOperatorMessages.ERROR_DOCUMENT_TOO_LARGE.rich(
            lang=user_with_settings.lang, limit_kb=MAX_DOCUMENT_BYTES // 1024
        ),
    )


async def test_document_rejected_when_not_utf8(
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
    tg_user: TgUser,
    app: StubMitupApp,
):
    register_member(user_with_settings)
    update = document_update(app, tg_user, file_size=None)
    set_downloaded_bytes(app, b"\xff\xfe\x00broken")
    handler_context.update = update

    context, state = await call_handler(BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(
        update, BroadcastOperatorMessages.ERROR_DOCUMENT_DECODE.rich(lang=user_with_settings.lang)
    )


# --- Catch-all for non-text, non-document input ---
@pytest.mark.parametrize("update", [UpdateRequest(message_text="anything")], indirect=True)
async def test_invalid_content_handler_reprompts(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    register_member: RegisterMember,
):
    register_member(user_with_settings)

    context, state = await call_handler(
        BroadcastHandlerId.BROADCAST_INVALID_CONTENT_MESSAGE, handler_context=handler_context
    )

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_called(
        update, BroadcastOperatorMessages.UPLOAD_PROMPT.rich(lang=user_with_settings.lang)
    )


# --- Non-member defensive path (member_user returns None) ---
@pytest.mark.parametrize(
    "handler_id, update",
    [
        pytest.param(
            BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE,
            UpdateRequest(message_text=PLAIN_YAML),
            id="content",
        ),
        pytest.param(
            BroadcastHandlerId.BROADCAST_INVALID_CONTENT_MESSAGE,
            UpdateRequest(location=Location(latitude=0, longitude=0)),
            id="invalid_content",
        ),
    ],
    indirect=["update"],
)
async def test_non_member_is_silently_ignored(
    handler_id: BroadcastHandlerId,
    update: Update,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
):
    """load_operator resolves via member_user; with no MEMBER row it returns None and the handler
    bails silently (the handler is already admin-gated by the registry)."""
    # Deliberately register no member row, so member_user resolves to None.
    context, state = await call_handler(handler_id, handler_context=handler_context)

    assert state == ConversationBroadcastState.AWAITING_CONTENT
    context.api.assert_send_message_not_called()
