from pathlib import Path

import yaml

from . import runner

CI_TEST_YML = Path(".gitlab/ci/test.yml")


def extract_ci_languages(path: Path) -> list[str]:
    with path.open() as test_yml:
        data = yaml.safe_load(test_yml)

    matrix = data.get("test", {}).get("parallel", {}).get("matrix", [])

    result: list[str] = []
    for entry in matrix:
        if "LANG" in entry:
            langs = entry["LANG"]
            result.extend(langs if isinstance(langs, list) else [langs])
    return result


def compare_languages(ci_languages: set[str], supported: set[str]) -> int:
    if ci_languages == supported:
        runner.console.print(f"OK — CI matrix matches SUPPORTED_LANGUAGES: {sorted(supported)}", markup=False)
        return 0

    missing = supported - ci_languages
    extra = ci_languages - supported

    runner.console.print("ERROR — CI language matrix does not match SUPPORTED_LANGUAGES.", markup=False)
    if missing:
        runner.console.print(f"  Missing from CI matrix: {sorted(missing)}", markup=False)
    if extra:
        runner.console.print(f"  Extra in CI matrix (not in SUPPORTED_LANGUAGES): {sorted(extra)}", markup=False)
    runner.console.print(f"\n  SUPPORTED_LANGUAGES: {sorted(supported)}", markup=False)
    runner.console.print(f"  CI matrix LANG values: {sorted(ci_languages)}", markup=False)
    runner.console.print("\n  Update .gitlab/ci/test.yml to match SUPPORTED_LANGUAGES.", markup=False)
    return 1


def run_check(root: Path) -> int:
    # Imported lazily so the mb CLI does not pay for (or depend on) the bot package
    # at import time; the workspace venv guarantees it is available when this runs.
    from mitup_bot.translations import SUPPORTED_LANGUAGES

    return compare_languages(set(extract_ci_languages(root / CI_TEST_YML)), set(SUPPORTED_LANGUAGES))
