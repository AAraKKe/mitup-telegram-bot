from pathlib import Path

from mb import language_matrix

TEST_YML = """
test-suite:
  parallel:
    matrix:
      - TARGET: [libs/core, tools/mb]
      - TARGET: [apps/bot]
        MARKER: ["not i18n"]
      - TARGET: [apps/bot]
        LOCALE: [en, es_ES]

test-db:
  script: uv run mb test --db
"""


def test_extract_language_matrices_reads_the_locale_axis(tmp_path: Path):
    path = tmp_path / "test.yml"
    path.write_text(TEST_YML)

    assert language_matrix.extract_language_matrices(path) == {"test-suite": ["en", "es_ES"]}


def test_extract_ci_languages_flattens_and_dedupes_across_jobs(tmp_path: Path):
    path = tmp_path / "test.yml"
    path.write_text(TEST_YML)

    assert language_matrix.extract_ci_languages(path) == ["en", "es_ES"]


def test_matching_languages_pass():
    assert language_matrix.compare_languages({"en", "es_ES"}, {"en", "es_ES"}) == 0


def test_missing_and_extra_languages_fail():
    assert language_matrix.compare_languages({"en", "fr_FR"}, {"en", "es_ES"}) == 1
