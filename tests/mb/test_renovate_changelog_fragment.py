import re
import subprocess
from pathlib import Path

import pytest

from mb import changelog_check, crowdin_mr_ops

SCRIPT = Path(__file__).parents[2] / "tools" / "renovate_changelog_fragment.sh"


@pytest.mark.parametrize(
    ("data", "slug", "expected_sentence"),
    [
        pytest.param(
            "renovate/python-dependencies\nDependencies are updated:\nruff to 0.17.0\nty to 0.0.9\nruff to 0.17.0\n",
            "renovate-python-dependencies",
            "Dependencies are updated: ruff to 0.17.0, ty to 0.0.9.",
            id="dependency-updates",
        ),
        pytest.param(
            "renovate/lock-file-maintenance\n"
            "The lock file is refreshed with the latest compatible dependency versions.\n",
            "renovate-lock-file-maintenance",
            "The lock file is refreshed with the latest compatible dependency versions.",
            id="lock-file-maintenance",
        ),
    ],
)
def test_script_writes_an_unnumbered_fragment_explaining_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: str, slug: str, expected_sentence: str
):
    data_file = tmp_path / "renovate-data.txt"
    data_file.write_text(data)
    monkeypatch.setenv("RENOVATE_POST_UPGRADE_COMMAND_DATA_FILE", str(data_file))
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()

    subprocess.run(["sh", str(SCRIPT)], cwd=tmp_path, check=True)

    [fragment] = fragments.iterdir()
    assert re.fullmatch(rf"\+{slug}-\d{{14}}\.internal\.md", fragment.name)
    assert changelog_check.FRAGMENT_NAME.fullmatch(fragment.name)
    text = fragment.read_text()
    note, sentence = text.splitlines()
    assert note == crowdin_mr_ops.FRAGMENT_TEXT.splitlines()[0]
    assert note.startswith("<!--")
    assert "`mb release prepare`" in note
    assert sentence == expected_sentence
    assert changelog_check.HTML_COMMENT.sub("", text) == f"{expected_sentence}\n"
