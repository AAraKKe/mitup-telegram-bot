from pathlib import Path

import yaml

from . import console

CI_TEST_YML = Path(".gitlab/ci/test.yml")


LOCALE_AXIS = "LOCALE"


def extract_language_matrices(path: Path) -> dict[str, list[str]]:
    """Map every job that carries a ``parallel.matrix`` LOCALE axis to its declared languages.

    The language explosion lives in one ``parallel.matrix`` block on the test job, keyed by
    ``LOCALE``; a job may also carry non-language matrix blocks, which contribute no languages.
    """
    with path.open() as test_yml:
        data = yaml.safe_load(test_yml)

    matrices: dict[str, list[str]] = {}
    for job_name, job in data.items():
        if not isinstance(job, dict):
            continue
        matrix = job.get("parallel", {}).get("matrix", [])
        langs: list[str] = []
        for entry in matrix:
            if isinstance(entry, dict) and LOCALE_AXIS in entry:
                value = entry[LOCALE_AXIS]
                langs.extend(value if isinstance(value, list) else [value])
        if langs:
            matrices[job_name] = langs
    return matrices


def extract_ci_languages(path: Path) -> list[str]:
    """Flatten the languages declared across every LOCALE matrix job (order-preserving, de-duplicated)."""
    seen: list[str] = []
    for langs in extract_language_matrices(path).values():
        for lang in langs:
            if lang not in seen:
                seen.append(lang)
    return seen


def compare_languages(ci_languages: set[str], supported: set[str]) -> int:
    if ci_languages == supported:
        console.success(f"CI matrix matches SUPPORTED_LANGUAGES: {sorted(supported)}")
        return 0

    missing = supported - ci_languages
    extra = ci_languages - supported

    console.error("CI language matrix does not match SUPPORTED_LANGUAGES.")
    if missing:
        console.raw(f"  Missing from CI matrix: {sorted(missing)}")
    if extra:
        console.raw(f"  Extra in CI matrix (not in SUPPORTED_LANGUAGES): {sorted(extra)}")
    console.raw(f"\n  SUPPORTED_LANGUAGES: {sorted(supported)}")
    console.raw(f"  CI matrix LANG values: {sorted(ci_languages)}")
    console.raw("\n  Update .gitlab/ci/test.yml to match SUPPORTED_LANGUAGES.")
    return 1


def run_check(root: Path) -> int:
    # Imported lazily so the mb CLI does not pay for (or depend on) the bot package
    # at import time; the workspace venv guarantees it is available when this runs.
    from mitup_bot.translations import SUPPORTED_LANGUAGES

    supported = set(SUPPORTED_LANGUAGES)
    matrices = extract_language_matrices(root / CI_TEST_YML)
    if not matrices:
        console.error(f"No {LOCALE_AXIS} matrix found in .gitlab/ci/test.yml (expected the test-suite job).")
        return 1
    # Every LOCALE matrix block must cover the full language set, not just the union across jobs.
    return max(compare_languages(set(langs), supported) for langs in matrices.values())
