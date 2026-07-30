"""Static sweeps over every emission site in the repository.

A runtime test only proves the contract on the path it exercises, and both rules these guard are
broken by adding a call site rather than by changing one: a single dimension built from a request
URL publishes the bot token, and a single narrative property is rewritten onto every record in its
flush window. Walking the source is what covers the site that has no test yet.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.helpers import MITUP_DIR

# The shippable members. `tests/` is excluded on purpose: a test may legitimately construct a
# token-shaped string to prove it never escapes.
SOURCE_ROOTS = ("apps", "libs", "tools")

DIMENSION_KEYWORDS = frozenset({"dimensions", "base_dimensions"})

# A dimension value that reads as a URL, a credential, or the bot-token shape (`<digits>:<secret>`).
LEAKY_LITERAL = re.compile(r"https?://|\d{5,}:[A-Za-z0-9_-]{20,}")

# An expression a dimension is derived from that names a URL, a header or a credential. The ADOT
# instrumentation leaked the token exactly this way — `RemoteOperation` was built from `request.url`.
LEAKY_EXPRESSION = re.compile(r"(?i)\b\w*(url|uri|token|secret|password|header|credential|api_key)\w*\b")

# `Fault` is the invocation outcome and its SampleCount is the fault-rate alarm's request
# denominator, so it has exactly one writer per runtime. `with_prefix` derivatives
# (`TelegramApiFault`) are separate series and are not covered by this rule.
FAULT_WRITERS = frozenset(
    {
        "apps/bot/mitup_bot/handlers/registry.py",
        "apps/events/mitup_bot/events/service.py",
    }
)

# Prose an EMF record may not carry: an update snapshot, a rendered payload, a list of English
# sentences. Each name is spelled here rather than imported, because the assertion is precisely
# that nothing in the tree defines them.
BANNED_RECORD_PROPERTIES = frozenset({"Update", "UpdatePayload", "failed_details"})

# The two mechanisms that attach that prose in bulk: a per-emission update snapshot, and a
# traceback copied onto every logger in the flush window.
BANNED_EMISSION_NAMES = frozenset({"include_update_properties", "add_stack_trace"})


def python_sources() -> list[Path]:
    return sorted(path for root in SOURCE_ROOTS for path in (MITUP_DIR / root).rglob("*.py"))


def parsed_sources() -> Iterator[tuple[Path, str, ast.Module]]:
    for path in python_sources():
        source = path.read_text()
        yield path, source, ast.parse(source, filename=str(path))


def where(path: Path, node: ast.stmt | ast.expr) -> str:
    return f"{path.relative_to(MITUP_DIR)}:{node.lineno}"


def keyword_values(tree: ast.Module, names: frozenset[str]) -> Iterator[ast.expr]:
    """Every `<name>=<expr>` keyword argument passed to any call."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in names:
                    yield keyword.value


def dict_literals(tree: ast.Module, names: frozenset[str]) -> Iterator[ast.Dict]:
    """Every `<name>={...}` keyword argument written as a dict literal."""
    for value in keyword_values(tree, names):
        if isinstance(value, ast.Dict):
            yield value


def test_no_emission_site_builds_a_dimension_from_a_url_or_a_credential():
    """The Bot API embeds the token in every request URL, so a dimension derived from one publishes
    it — non-redactable, retained 15 months, readable with view-only access. Outbound telemetry
    records `api_method` and nothing else about the request."""
    offenders: list[str] = []

    def inspect(path: Path, source: str, node: ast.expr | None):
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if LEAKY_LITERAL.search(node.value):
                offenders.append(f"{where(path, node)}: literal {node.value!r}")
            return
        segment = ast.get_source_segment(source, node) or ""
        if LEAKY_EXPRESSION.search(segment):
            offenders.append(f"{where(path, node)}: {segment}")

    for path, source, tree in parsed_sources():
        for value in keyword_values(tree, DIMENSION_KEYWORDS):
            if isinstance(value, ast.Dict):
                # Both halves: a `{"RemoteOperation": request.url}` leaks through its value, a
                # `{url: "x"}` through its key.
                for node in (*value.keys, *value.values):
                    inspect(path, source, node)
            else:
                # `dimensions=phase_dims(mode, table)` — the dict is built elsewhere, so the call
                # expression is all this sweep can see.
                inspect(path, source, value)

    assert not offenders, "dimensions derived from a URL or a credential:\n" + "\n".join(offenders)


def test_fault_has_exactly_one_writer_per_runtime():
    """A second writer serialises `Fault` as an array, which the `filter Fault = 1` triage query
    stops matching and which halves the value the fault-rate alarm reads on twice the samples."""
    writers: dict[str, int] = {}

    for path, _, tree in parsed_sources():
        # `MetricKey.FAULT.with_prefix("TelegramApi")` names a different series (`TelegramApiFault`)
        # and is not bound by the single-writer rule, so its receiver is excluded.
        prefixed = {
            id(node.func.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "with_prefix"
        }

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == "FAULT" and id(node) not in prefixed):
                continue
            if isinstance(node.value, ast.Name) and node.value.id == "MetricKey":
                writers.setdefault(str(path.relative_to(MITUP_DIR)), node.lineno)

    assert set(writers) == FAULT_WRITERS, f"MetricKey.FAULT written outside the invocation wrappers: {writers}"


def test_no_record_carries_narrative_properties():
    """An EMF record is an index entry. Prose on it is rewritten onto every record in the flush
    window, cannot be alarmed on, and duplicates what the correlated log lines already say."""
    offenders: list[str] = []

    for path, _, tree in parsed_sources():
        for properties in dict_literals(tree, frozenset({"properties"})):
            for key in properties.keys:
                if isinstance(key, ast.Constant) and key.value in BANNED_RECORD_PROPERTIES:
                    offenders.append(f"{where(path, key)}: property {key.value!r}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                passed = next((kw.arg for kw in node.keywords if kw.arg in BANNED_EMISSION_NAMES), None)
                if passed is not None:
                    offenders.append(f"{where(path, node)}: {passed}")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in BANNED_EMISSION_NAMES:
                offenders.append(f"{where(path, node)}: {node.name}")

    assert not offenders, "narrative on the metric plane:\n" + "\n".join(offenders)


@pytest.mark.parametrize("root", SOURCE_ROOTS)
def test_the_sweeps_actually_read_the_source(root: str):
    """Each assertion above passes vacuously if the walk finds nothing, and a member moving under a
    new layout would silence all three at once."""
    assert list((MITUP_DIR / root).rglob("*.py")), f"no Python sources found under {root}/"
