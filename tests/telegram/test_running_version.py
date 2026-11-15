import pytest

from mitup_bot import running_version


@pytest.fixture(autouse=True)
def restore_running_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(running_version.RunningVersionState, "version", None)


def test_configure_adopts_the_release_tag():
    running_version.configure("v3.0.0")

    assert running_version.RunningVersionState.version == "v3.0.0"


@pytest.mark.parametrize("version", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_deployment_that_names_no_version_leaves_it_unknown(version: str | None):
    running_version.configure(version)

    assert running_version.RunningVersionState.version is None
