from pathlib import Path

from mb import language_matrix

TEST_YML = """
test:
  parallel:
    matrix:
      - LANG: [en, es_ES]
"""


def test_extract_ci_languages_reads_the_test_matrix(tmp_path: Path):
    path = tmp_path / "test.yml"
    path.write_text(TEST_YML)

    assert language_matrix.extract_ci_languages(path) == ["en", "es_ES"]


def test_matching_languages_pass():
    assert language_matrix.compare_languages({"en", "es_ES"}, {"en", "es_ES"}) == 0


def test_missing_and_extra_languages_fail():
    assert language_matrix.compare_languages({"en", "fr_FR"}, {"en", "es_ES"}) == 1
