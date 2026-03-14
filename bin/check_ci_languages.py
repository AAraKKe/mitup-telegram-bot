#!/usr/bin/env python3
"""
Check that the languages in the CI test matrix match SUPPORTED_LANGUAGES.

Parses .gitlab/ci/test.yml and extracts the LANG matrix values from the
`test` job. Compares them against SUPPORTED_LANGUAGES and exits with an
error if they differ.

Exit codes:
    0 – CI matrix matches SUPPORTED_LANGUAGES exactly
    1 – languages are missing or extra in the CI matrix
"""

import sys
from pathlib import Path

import yaml

from mitup_bot.translations import SUPPORTED_LANGUAGES

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_TEST_YML = REPO_ROOT / ".gitlab" / "ci" / "test.yml"


def extract_ci_languages(path: Path) -> list[str]:
    with path.open() as f:
        data = yaml.safe_load(f)

    test_job = data.get("test", {})
    parallel = test_job.get("parallel", {})
    matrix = parallel.get("matrix", [])

    result: list[str] = []
    for entry in matrix:
        if "LANG" in entry:
            langs = entry["LANG"]
            result.extend(langs if isinstance(langs, list) else [langs])
    return result


def main() -> int:
    ci_languages = set(extract_ci_languages(CI_TEST_YML))
    supported = set(SUPPORTED_LANGUAGES)

    if ci_languages == supported:
        print(f"OK — CI matrix matches SUPPORTED_LANGUAGES: {sorted(supported)}")
        return 0

    missing = supported - ci_languages
    extra = ci_languages - supported

    print("ERROR — CI language matrix does not match SUPPORTED_LANGUAGES.")
    if missing:
        print(f"  Missing from CI matrix: {sorted(missing)}")
    if extra:
        print(f"  Extra in CI matrix (not in SUPPORTED_LANGUAGES): {sorted(extra)}")
    print(f"\n  SUPPORTED_LANGUAGES: {sorted(supported)}")
    print(f"  CI matrix LANG values: {sorted(ci_languages)}")
    print("\n  Update .gitlab/ci/test.yml to match SUPPORTED_LANGUAGES.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
