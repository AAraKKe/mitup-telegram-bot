"""Redeeming a Patreon pairing code: claim, confirm, and everything that refuses to link.

Two properties carry the security of this flow and both are pinned here. A pairing code only opens a
prompt, so a finish link passed on to somebody else costs them nothing but a tap on Not now. And the
confirm button is addressed by the code rather than by a row id, with the consume statement also
requiring the confirming account to be the one that claimed the row, so a forged confirm naming
another user's pending link writes nothing.
"""

import datetime as dt
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from sqlmodel import select
from telegram import Chat, Message, Update
from telegram.ext import CommandHandler

from mitup_bot import patreon, supporter
from mitup_bot.config import LimitsConfig, PatreonConfig
from mitup_bot.handlers.collaborate.enums import CollaborateHandlerId
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import PatreonPendingLink, SupporterSubscription, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.patreon import PatreonRuntime, pairing
from mitup_bot.patreon.pairing import PAIRING_DEEP_LINK_PREFIX
from mitup_bot.patreon.pending_links import (
    claim_statement,
    classification_statement,
    consume_statement,
    spent_link_statement,
)
from mitup_bot.patreon_link import LinkOutcome
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import CollaborateMessages, PrivacyMessages, SupporterNotificationMessages
from mitup_bot.views.collaborate import (
    collaborate_linked_patron_view,
    link_confirmation_view,
    patreon_already_linked_elsewhere_view,
    patreon_link_code_not_valid_view,
    patreon_link_declined_view,
    patreon_link_needs_setup_view,
)
from tests.helpers import (
    HandlerContext,
    UpdateRequest,
    call_handler,
    create_patreon_config,
    create_supporter_subscription,
)
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

PAIRING_CODE = "pairing-code_v1"
PATRON_ACTIVE_MEETINGS = 12
PATRON_SCHEDULING_DAYS = 200
PATREON_NAME = "Ada Lovelace"

# The `/start` a tap on the result page's button produces, and the confirm/decline taps that follow.
REDEEM_UPDATE = UpdateRequest(command="start", command_args=f"{PAIRING_DEEP_LINK_PREFIX}_{PAIRING_CODE}")
CONFIRM_UPDATE = UpdateRequest(callback_query=cb.CONFIRM_PATREON_LINK.with_code(PAIRING_CODE))
DECLINE_UPDATE = UpdateRequest(callback_query=cb.DECLINE_PATREON_LINK.with_code(PAIRING_CODE))


@pytest.fixture
def patreon_config() -> Iterator[PatreonConfig]:
    saved = PatreonRuntime.config
    config = create_patreon_config(campaign_id="12345")
    patreon.configure(config)
    try:
        yield config
    finally:
        PatreonRuntime.config = saved


@pytest.fixture(autouse=True)
def patron_caps(monkeypatch: pytest.MonkeyPatch):
    """Pin the Patron-tier caps so the Collaborate copy renders deterministic numbers."""
    config = LimitsConfig(
        patron_active_meetings=PATRON_ACTIVE_MEETINGS,
        patron_scheduling_horizon_days=PATRON_SCHEDULING_DAYS,
    )
    monkeypatch.setattr(supporter.PolicyState, "config", config)


def register_member(mock_session: MockDbSession, user: User):
    """Make ``user`` resolvable through the lookups the redemption handlers perform."""
    user.status = UserStatus.MEMBER
    mock_session.add_user(user)
    member_lookup = select(User).where(User.tg_user_id == user.tg_user_id, User.status == UserStatus.MEMBER)
    mock_session.add_objects_with_statement(member_lookup, (user,))


def claimable(mock_session: MockDbSession, user: User, patreon_user_id: str, level: SupporterLevel):
    """Make the pairing code claimable by ``user``, as if a browser had just completed the consent."""
    mock_session.add_objects_with_statement(
        claim_statement(PAIRING_CODE, user.tg_user_id), ((patreon_user_id, PATREON_NAME, level),)
    )


def confirmable(mock_session: MockDbSession, tg_user_id: int, patreon_user_id: str, level: SupporterLevel):
    """Make the pairing code consumable by ``tg_user_id``, as if they had already claimed it."""
    mock_session.add_objects_with_statement(
        consume_statement(PAIRING_CODE, tg_user_id), ((patreon_user_id, PATREON_NAME, level),)
    )


def added_subscriptions(mock_session: MockDbSession) -> list[SupporterSubscription]:
    return [obj for obj in mock_session.objects_added if isinstance(obj, SupporterSubscription)]


def stored_pending_row(
    *, claimed_tg_user_id: int | None = None, spent: bool = False, patreon_user_id: str = "p-redeemed"
) -> PatreonPendingLink:
    """The pairing code's row as the classification and spent-link reads would return it."""
    return PatreonPendingLink(
        code_hash=pairing.hash_pairing_code(PAIRING_CODE),
        patreon_user_id=patreon_user_id,
        patreon_full_name=PATREON_NAME,
        supporter_level=SupporterLevel.HOST_2,
        expiration=dt.datetime(2026, 7, 5, 12, 0),
        claimed_tg_user_id=claimed_tg_user_id,
        consumed_time=dt.datetime(2026, 7, 5, 12, 5) if spent else None,
    )


# --- Claiming opens a prompt and writes nothing ---


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_claiming_prompts_and_writes_nothing(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # The property that closes the reverse attack: a finish link handed to somebody else gets them
    # a question, not a link. Nothing is written until they answer it.
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    assert not added_subscriptions(mock_session)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    metrics.assert_emitted(name=MetricKey.PATREON_LINK_PROMPTED, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)
    view = context.api.call_args("send_message").kwargs["view"]
    assert PATREON_NAME in view.description.text


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_prompt_leads_with_the_patreon_account_name(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    # The prompt only protects anyone if the name is read first, so it opens the message.
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    view = context.api.call_args("send_message").kwargs["view"]
    assert view.description.text.startswith(PATREON_NAME)
    assert view.description == CollaborateMessages.LINK_CONFIRM.get(
        lang=user_with_settings.lang, patreon_name=PATREON_NAME
    )
    assert "${" not in view.description.text


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_prompt_warns_louder_when_a_different_patreon_is_already_linked(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    # The destructive case. The warning is chosen from the tier the user holds *now*, read live,
    # and names that tier so a Host reads the word for what confirming would cost them.
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-stranger", SupporterLevel.NONE)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    view = context.api.call_args("send_message").kwargs["view"]
    assert view.description == CollaborateMessages.LINK_CONFIRM_REPLACES.get(
        lang=user_with_settings.lang,
        patreon_name=PATREON_NAME,
        current_tier=CollaborateMessages.TIER_NAME_HOST_2.get_text(lang=user_with_settings.lang),
    )
    assert "Gamemaster" in view.description.text
    assert "${" not in view.description.text


# --- The two properties the prompt copy has to hold, in every language ---
#
# These two are the only strings here whose exact wording is a security control, and they are
# checked in all six languages rather than in English alone. Translated catalogs are rewritten by
# an automated sync on a schedule: a `msgstr` can change with no Python diff and no review, and
# catalog validation compares msgid *sets*, so a rewritten translation passes it untouched. English
# is the one language where a person would likely notice a violation by eye, which is exactly why
# pinning English alone would leave the property guarded where it needs guarding least.
#
# Every fragment below is a hardcoded literal. Building the expected text through
# `CollaborateMessages...get(lang=...)` would produce it with the same call the handler makes, and
# the assertion could then never fail; these tables are an independent statement of what the copy
# has to say. Each language's phrases are read off *its own* sentences rather than translated from a
# shared template, because the line between a forbidden framing and a correct one falls in a
# different place in each language.
#
# A translator rephrasing any of this will break these tests. That is the purpose: it puts a person
# in front of a change to security-critical copy that would otherwise land unread at the top of the
# hour. The repair is to read the new wording and update the table, never to widen the entries until
# they stop tripping.
#
# Every entry is lowercase, because both checks compare against lowercased text.


class AnchorCopy(NamedTuple):
    """What the anchor sentence has to carry in one language, and how it realistically degrades.

    ``reflexive`` is the load-bearing half: "after you approved it yourself" is a question only the
    real patron can answer, while a merely temporal "after it was approved" is one the attacker has
    already satisfied on the reader's behalf. ``follow_up`` is the sentence that tells the reader
    what it means if they did not do it.

    ``smoothed_rendering`` keeps ``follow_up`` intact and drops only the emphasis -- what a
    translator tidying an awkward sentence would produce, rather than an arbitrary bad string. Its
    control asserts that exactly ``reflexive`` goes missing, so it demonstrates the check reacting
    to that specific loss instead of to any difference at all.
    """

    reflexive: str
    follow_up: str
    smoothed_rendering: str


class ForbiddenPossessive(NamedTuple):
    """A declarative possessive for the incoming account, and the rendering that would introduce it.

    ``violating_rendering`` is the language's own correct sentence with the demonstrative swapped
    for the possessive: the minimal change a translator would actually make. An absence assertion
    needs a companion demonstrating the check fires on the specific failure it exists to prevent,
    not on any failure -- handed the bare phrase it would only prove that substring matching works.
    A phrase added without a rendering is an untested guess, which is why the two are one entry.
    """

    phrase: str
    violating_rendering: str


ANCHOR_COPY: dict[str, AnchorCopy] = {
    "en": AnchorCopy(
        "approving mitup on patreon yourself",
        "if you didn't just do that",
        "You should only be seeing this straight after approving Mitup on Patreon. "
        "If you didn't just do that, somebody else's link reached this chat.",
    ),
    "es_ES": AnchorCopy(
        "haber aprobado tú mitup en patreon",
        "si no acabas de hacerlo",
        "Solo deberías estar viendo esto justo después de haber aprobado Mitup en Patreon. "
        "Si no acabas de hacerlo, el enlace de otra persona ha llegado a este chat.",
    ),
    "gl_ES": AnchorCopy(
        "seres ti quen autorizou mitup en patreon",
        "se non acabas de facelo",
        "Isto só debería aparecerche xusto despois de autorizar Mitup en Patreon. "
        "Se non acabas de facelo, chegou a este chat a ligazón doutra persoa.",
    ),
    "de_DE": AnchorCopy(
        "gerade eben selbst mitup auf patreon freigegeben",
        "wenn du das nicht gerade getan hast",
        "Du solltest das hier nur sehen, wenn du gerade eben Mitup auf Patreon freigegeben hast. "
        "Wenn du das nicht gerade getan hast, ist der Link von jemand anderem in diesem Chat gelandet.",
    ),
    "pt_BR": AnchorCopy(
        "você mesmo autorizar o mitup no patreon",
        "se você não acabou de fazer isso",
        "Você só deveria estar vendo isto logo depois de autorizar o Mitup no Patreon. "
        "Se você não acabou de fazer isso, o link de outra pessoa chegou a este chat.",
    ),
    "it_IT": AnchorCopy(
        "autorizzato tu stesso mitup su patreon",
        "se non l'hai appena fatto",
        "Dovresti vedere questo messaggio solo subito dopo aver autorizzato Mitup su Patreon. "
        "Se non l'hai appena fatto, è arrivato in questa chat il link di qualcun altro.",
    ),
}

# The declarative framings that would hand the incoming account to the reader: the informal and the
# formal possessive attached to the account noun, and nothing broader. A *conditional* possessive is
# a different sentence and a correct one -- "only if you recognise the name above as your own
# Patreon" asks the reader to make the claim instead of making it for them -- and five of the six
# languages separate the two by a word the declarative lacks: own, propia, túa propia, própria,
# eigenes. The formal register is listed alongside the informal because a translator switching
# register is precisely the plausible mutation this check exists to catch, and a table holding only
# the informal form would wave the formal one through.
#
# The one thing a phrase list cannot tell apart is which account a possessive attaches to. The
# replacement variant also speaks about the account the reader really backs Mitup with, and that one
# is theirs; every catalog currently words it without a possessive ("the Patreon account you
# actually back Mitup with"), so nothing here matches it. A translator who possessivised that clause
# would trip this check on correct copy -- read the sentence, confirm it is about the existing
# account, and exempt it deliberately rather than dropping the phrase that caught it.
FORBIDDEN_POSSESSIVES: dict[str, tuple[ForbiddenPossessive, ...]] = {
    "en": (
        ForbiddenPossessive("your patreon account", "That is your Patreon account this link would connect to Mitup."),
    ),
    "es_ES": (
        ForbiddenPossessive(
            "tu cuenta de patreon", "Esa es tu cuenta de Patreon que este enlace conectaría con Mitup."
        ),
        ForbiddenPossessive(
            "su cuenta de patreon", "Esa es su cuenta de Patreon que este enlace conectaría con Mitup."
        ),
    ),
    "gl_ES": (
        ForbiddenPossessive(
            "túa conta de patreon", "Esa é a túa conta de Patreon que esta ligazón conectaría con Mitup."
        ),
        ForbiddenPossessive(
            "súa conta de patreon", "Esa é a súa conta de Patreon que esta ligazón conectaría con Mitup."
        ),
    ),
    # German declines, so the same possessive reaches the account noun in two shapes: the nominative
    # of "Das ist das Patreon-Konto..." and the dative of the same sentence turned around into
    # "Dieser Link würde Mitup mit ... verbinden". Both are listed, in both registers.
    "de_DE": (
        ForbiddenPossessive(
            "dein patreon-konto", "Das ist dein Patreon-Konto, das dieser Link mit Mitup verbinden würde."
        ),
        ForbiddenPossessive("deinem patreon-konto", "Dieser Link würde Mitup mit deinem Patreon-Konto verbinden."),
        ForbiddenPossessive(
            "ihr patreon-konto", "Das ist Ihr Patreon-Konto, das dieser Link mit Mitup verbinden würde."
        ),
        ForbiddenPossessive("ihrem patreon-konto", "Dieser Link würde Mitup mit Ihrem Patreon-Konto verbinden."),
    ),
    "pt_BR": (
        ForbiddenPossessive("sua conta do patreon", "Essa é a sua conta do Patreon que este link conectaria ao Mitup."),
        ForbiddenPossessive("tua conta do patreon", "Essa é a tua conta do Patreon que este link conectaria ao Mitup."),
    ),
    # Italian is the language that refuses a shared template. Its correct copy says "come il tuo
    # Patreon" for the conditional and "il tuo badge" about the reader's own badge, so anything
    # looser than the account noun from Italian's own declarative sentence ("Questo è l'account
    # Patreon...") would flag copy that is right.
    "it_IT": (
        ForbiddenPossessive(
            "tuo account patreon", "Questo è il tuo account Patreon che questo link collegherebbe a Mitup."
        ),
        ForbiddenPossessive(
            "suo account patreon", "Questo è il suo account Patreon che questo link collegherebbe a Mitup."
        ),
    ),
}


def missing_anchor_fragments(text: str, lang: str) -> list[str]:
    """The fragments of the anchor sentence for ``lang`` that ``text`` fails to carry."""
    anchor = ANCHOR_COPY[lang]
    return [fragment for fragment in (anchor.reflexive, anchor.follow_up) if fragment not in text.lower()]


def possessive_violations(text: str, lang: str) -> list[str]:
    """The forbidden declarative possessives for ``lang`` that ``text`` contains."""
    return [entry.phrase for entry in FORBIDDEN_POSSESSIVES[lang] if entry.phrase in text.lower()]


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
@pytest.mark.parametrize("current_level", [SupporterLevel.NONE, SupporterLevel.HOST_2])
async def test_both_prompt_variants_anchor_on_something_the_attacker_cannot_forge(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    current_level: SupporterLevel,
):
    # An attacker picks their own Patreon display name, so "do you recognise this?" is a question
    # they can answer for the reader. Whether the reader just approved something on Patreon is not,
    # because in the forwarded-link case the attacker did that part. Both variants must say so, in
    # whichever language the reader is being asked.
    user_with_settings.supporter_level = current_level
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-stranger", SupporterLevel.NONE)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    text = context.api.call_args("send_message").kwargs["view"].description.text
    assert missing_anchor_fragments(text, user_with_settings.lang) == []


def test_the_anchor_fragments_catch_the_emphasis_being_smoothed_away(lang: str):
    # The control for the assertion above, against the realistic degradation rather than a deleted
    # sentence: the anchor rephrased without its emphatic word, everything else intact. Exactly the
    # emphasis must come back missing -- the follow-up still being found is what shows the check
    # reacting to the loss that matters and not merely to a shorter string.
    anchor = ANCHOR_COPY[lang]
    assert missing_anchor_fragments(anchor.smoothed_rendering, lang) == [anchor.reflexive]


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
@pytest.mark.parametrize("current_level", [SupporterLevel.NONE, SupporterLevel.HOST_2])
async def test_prompt_never_calls_the_incoming_account_the_readers_own(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    current_level: SupporterLevel,
):
    # "your Patreon account" would pre-frame a stranger's account as the reader's and defeat the
    # check the prompt exists to make. The copy names it demonstratively instead. Both variants are
    # read because the replacement one is where the correct conditional possessive lives, so this
    # also holds the forbidden list to phrases narrow enough to leave that sentence alone.
    user_with_settings.supporter_level = current_level
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    text = context.api.call_args("send_message").kwargs["view"].description.text
    assert possessive_violations(text, user_with_settings.lang) == []


def test_the_forbidden_possessives_catch_the_swap_that_would_introduce_them(lang: str):
    # The control for the assertion above, and the reason that assertion means anything: an absence
    # check reports green both when the copy is right and when the phrase it looks for could never
    # have appeared -- a misspelling, or a formal register nobody listed. So each phrase is shown
    # catching this language's own sentence with the demonstrative swapped for that possessive,
    # which is the change that would actually be made.
    for entry in FORBIDDEN_POSSESSIVES[lang]:
        assert possessive_violations(entry.violating_rendering, lang) == [entry.phrase]


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_prompt_buttons_carry_the_code_and_no_row_id(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    register_member(mock_session, user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    confirm, decline = context.api.call_args("send_message").kwargs["view"].keyboard[0]
    assert confirm.callback_data == cb.CONFIRM_PATREON_LINK.with_code(PAIRING_CODE)
    assert decline.callback_data == cb.DECLINE_PATREON_LINK.with_code(PAIRING_CODE)
    # A row id in the callback would be a small guessable integer; the code is the address instead.
    assert confirm.callback_data.id is None
    assert len(str(confirm.callback_data).encode()) <= 64


# --- Refusals, each leaving the pending row untouched ---


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_unknown_code_explains_itself_and_links_nothing(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # Nothing is claimable, so the claim matches no row. Expired, already-consumed and
    # claimed-by-somebody-else arrive here by the same route.
    register_member(mock_session, user_with_settings)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    context.api.assert_send_message_called(update, patreon_link_code_not_valid_view(user_with_settings.lang))
    assert not added_subscriptions(mock_session)
    metrics.assert_emitted(name=MetricKey.PATREON_LINK_REFUSED, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


@pytest.mark.parametrize(
    "update", [UpdateRequest(command="start", command_args=PAIRING_DEEP_LINK_PREFIX)], indirect=True
)
async def test_pairing_link_without_a_code_is_answered_not_ignored(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    # A truncated deep link carries the prefix and no token. It must not fall through to the main
    # menu, which would look like the link had worked.
    register_member(mock_session, user_with_settings)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    context.api.assert_send_message_called(update, patreon_link_code_not_valid_view(user_with_settings.lang))


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_a_code_from_someone_not_set_up_is_answered_and_the_row_is_left_alone(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # Onboarding claims every non-MEMBER `/start` and knows nothing about payloads, so without this
    # handler binding ahead of it the code would vanish. The pending row is not even claimed, so the
    # same link still works once they are set up.
    user_with_settings.status = UserStatus.JOINED_ONLY
    mock_session.add_user(user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    context.api.assert_send_message_called(update, patreon_link_needs_setup_view(user_with_settings.lang))
    assert not added_subscriptions(mock_session)
    metrics.assert_emitted(name=MetricKey.PATREON_LINK_REFUSED, dimensions={"Feature": str(Feature.PATREON_LINK)})


@pytest.mark.parametrize("update", [REDEEM_UPDATE], indirect=True)
async def test_a_code_from_an_account_being_deleted_is_refused(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    user_with_settings.status = UserStatus.DELETION_REQUESTED
    mock_session.add_user(user_with_settings)
    claimable(mock_session, user_with_settings, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_REDEEM, handler_context=handler_context)

    context.api.assert_send_message_called(
        update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang=user_with_settings.lang)
    )
    assert not added_subscriptions(mock_session)


# --- Confirming ---


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_confirming_links_to_the_confirming_account(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    added = added_subscriptions(mock_session)
    assert len(added) == 1
    assert added[0].user_id == user_with_settings.db_id
    assert added[0].patreon_user_id == "p-redeemed"
    assert user_with_settings.supporter_level is SupporterLevel.HOST_2
    metrics.assert_emitted(name=MetricKey.FLOW_COMPLETED, dimensions={"Feature": str(Feature.PATREON_LINK)})


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_confirming_grants_the_tier_from_the_row(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    # The row is the only authority for what is granted. The user currently holds a higher tier, and
    # that live value must not leak into the write.
    user_with_settings.supporter_level = SupporterLevel.HOST_3
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-redeemed", SupporterLevel.HOST_1)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    assert user_with_settings.supporter_level is SupporterLevel.HOST_1
    context.api.assert_send_message_to_user_called(
        user=user_with_settings,
        view=link_confirmation_view(
            SupporterNotificationMessages.unlocked_for(SupporterLevel.HOST_1).get(lang=user_with_settings.lang),
            user_with_settings.lang,
        ),
    )


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_a_forged_confirm_for_another_users_pending_link_writes_nothing(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # The regression that would silently reopen the original hole. Callback data is client-supplied,
    # so a forger can name any code they like; the consume statement also requires the row to have
    # been claimed by the confirming account, so this one matches nothing.
    register_member(mock_session, user_with_settings)
    other_tg_user_id = user_with_settings.tg_user_id + 1
    confirmable(mock_session, other_tg_user_id, "p-someone-elses", SupporterLevel.HOST_3)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    assert not added_subscriptions(mock_session)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    context.api.assert_edit_message_called(update, patreon_link_code_not_valid_view(user_with_settings.lang))
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_confirming_a_spent_link_says_so_instead_of_crashing(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # A double tap, a back button, or a prompt confirmed twice all land here. The happy path never
    # produces this branch, which is why it needs its own test.
    register_member(mock_session, user_with_settings)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    context.api.assert_edit_message_called(update, patreon_link_code_not_valid_view(user_with_settings.lang))
    assert not added_subscriptions(mock_session)
    metrics.assert_emitted(name=MetricKey.PATREON_LINK_REFUSED, dimensions={"Feature": str(Feature.PATREON_LINK)})


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_confirming_shows_the_linked_collaborate_screen(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
):
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-own", SupporterLevel.HOST_2)
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="p-own")
    mock_session.add_object(subscription, "user_id")
    mock_session.add_object(subscription, "patreon_user_id")

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        collaborate_linked_patron_view(
            user_with_settings.lang, SupporterLevel.HOST_2, PATRON_ACTIVE_MEETINGS, PATRON_SCHEDULING_DAYS
        ),
    )


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_already_linked_elsewhere_explains_itself(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-shared", SupporterLevel.HOST_2)
    # The same Patreon account already backs a different Mitup account.
    other = create_supporter_subscription(user_id=user_with_settings.db_id + 1, patreon_user_id="p-shared")
    mock_session.add_object(other, "patreon_user_id")

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert view == patreon_already_linked_elsewhere_view(user_with_settings.lang)
    assert view.description == CollaborateMessages.LINK_ALREADY_LINKED_ELSEWHERE.get(lang=user_with_settings.lang)
    assert "${" not in view.description.text
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_a_forged_confirm_around_the_claim_step_is_metered_as_unclaimed(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # A confirm carrying a live code nobody claimed cannot come from an honest tap — the button
    # only exists after a claim succeeds — so its refusal must not be metered as an expired code,
    # which has an honest baseline. The user-facing answer stays the same vague message.
    register_member(mock_session, user_with_settings)
    mock_session.add_objects_with_statement(
        classification_statement(PAIRING_CODE), ((stored_pending_row(claimed_tg_user_id=None), False),)
    )

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    context.api.assert_edit_message_called(update, patreon_link_code_not_valid_view(user_with_settings.lang))
    assert not added_subscriptions(mock_session)
    metrics.assert_emitted(
        name=MetricKey.PATREON_LINK_REFUSED,
        dimensions={"Feature": str(Feature.PATREON_LINK)},
        properties={"outcome": "code_not_usable:consume:unclaimed"},
    )


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_a_double_tap_after_success_keeps_the_linked_screen(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    # The losing tap of a double tap arrives after its winner already linked and rendered the
    # Collaborate screen. Replacing that with "no longer works — nothing changed" would be false on
    # both counts, so the account that spent this exact code sees the linked screen instead, and
    # nothing new is metered: the completed flow was already counted once.
    user_with_settings.supporter_level = SupporterLevel.HOST_2
    register_member(mock_session, user_with_settings)
    mock_session.add_objects_with_statement(
        spent_link_statement(PAIRING_CODE, user_with_settings.tg_user_id),
        (stored_pending_row(claimed_tg_user_id=user_with_settings.tg_user_id, spent=True, patreon_user_id="p-own"),),
    )
    subscription = create_supporter_subscription(user_id=user_with_settings.db_id, patreon_user_id="p-own")
    mock_session.add_object(subscription, "user_id")

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        collaborate_linked_patron_view(
            user_with_settings.lang, SupporterLevel.HOST_2, PATRON_ACTIVE_MEETINGS, PATRON_SCHEDULING_DAYS
        ),
    )
    metrics.assert_not_emitted(name=MetricKey.PATREON_LINK_REFUSED)
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


@pytest.mark.parametrize("update", [CONFIRM_UPDATE], indirect=True)
async def test_a_link_refused_for_pending_deletion_never_renders_as_completed(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    # guards.current_user rejects a deletion-requested account before the consume runs, so the
    # refusal is produced directly here: the backstop arm must answer with the deletion alert and
    # never with a Collaborate screen implying a link happened.
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-redeemed", SupporterLevel.HOST_2)

    async def refuse_link(*args: object, **kwargs: object) -> LinkOutcome:
        return LinkOutcome.PENDING_DELETION

    monkeypatch.setattr("mitup_bot.patreon_link.link_patreon_account", refuse_link)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_CONFIRM, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang=user_with_settings.lang)
    )
    metrics.assert_emitted(name=MetricKey.PATREON_LINK_REFUSED, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)


# --- Where the redemption command binds ---


def chat_start_update(chat_type: str, text: str) -> Update:
    message = Message(message_id=1, date=dt.datetime(2026, 7, 5, 12, 0), chat=Chat(id=1, type=chat_type), text=text)
    return Update(update_id=1, message=message)


def test_redemption_start_binds_to_private_chats_only():
    # The deep link always opens the user's own chat with the bot, so a pairing command in a group
    # can only be a paste — and answering it there would render the prompt, with the Patreon name
    # on it, to everyone present and hand its buttons to any member.
    handler = HandlersRegistry.get_handler(CollaborateHandlerId.PATREON_LINK_REDEEM)
    assert isinstance(handler, CommandHandler)
    text = f"/start {PAIRING_DEEP_LINK_PREFIX}_{PAIRING_CODE}"

    assert handler.filters.check_update(chat_start_update(Chat.PRIVATE, text))
    assert not handler.filters.check_update(chat_start_update(Chat.GROUP, text))
    assert not handler.filters.check_update(chat_start_update(Chat.SUPERGROUP, text))


# --- Declining ---


@pytest.mark.parametrize("update", [DECLINE_UPDATE], indirect=True)
async def test_declining_writes_nothing_and_says_so(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    patreon_config: PatreonConfig,
    metrics: MetricAssertions,
):
    register_member(mock_session, user_with_settings)
    confirmable(mock_session, user_with_settings.tg_user_id, "p-redeemed", SupporterLevel.HOST_2)

    context, _ = await call_handler(CollaborateHandlerId.PATREON_LINK_DECLINE, handler_context=handler_context)

    context.api.assert_edit_message_called(update, patreon_link_declined_view(user_with_settings.lang))
    assert not added_subscriptions(mock_session)
    assert user_with_settings.supporter_level is SupporterLevel.NONE
    metrics.assert_emitted(name=MetricKey.FEATURE_CANCELLED, dimensions={"Feature": str(Feature.PATREON_LINK)})
    metrics.assert_not_emitted(name=MetricKey.FLOW_COMPLETED)
