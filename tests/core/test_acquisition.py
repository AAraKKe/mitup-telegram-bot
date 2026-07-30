import pytest

from mitup_bot.acquisition import SHARED_CARD_SOURCE, AcquisitionSource, normalize_acquisition_source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, AcquisitionSource.ORGANIC),
        (SHARED_CARD_SOURCE, AcquisitionSource.SHARED_CARD),
        ("inline", AcquisitionSource.INLINE),
        ("patreonlink_abc123", AcquisitionSource.OTHER),
        ("src_web", AcquisitionSource.WEB),
        ("src_patreon", AcquisitionSource.PATREON),
        ("src_footer", AcquisitionSource.FOOTER),
        ("src_directory", AcquisitionSource.DIRECTORY),
        ("src_unheardof", AcquisitionSource.OTHER),
        ("src", AcquisitionSource.OTHER),
        ("wat_ever", AcquisitionSource.OTHER),
        ("", AcquisitionSource.OTHER),
    ],
    ids=[
        "no-payload",
        "shared-card",
        "inline",
        "patreon-pairing-link",
        "campaign-web",
        "campaign-patreon",
        "campaign-footer",
        "campaign-directory",
        "unknown-campaign-token",
        "campaign-without-token",
        "unknown-kind",
        "empty",
    ],
)
def test_every_stored_value_normalizes_into_the_closed_vocabulary(raw: str | None, expected: AcquisitionSource):
    """The pairing link is the deliberate `other`: the bot recognizes the kind, but following it
    requires an account that is already linked, so it names no arrival worth a bucket."""
    assert normalize_acquisition_source(raw) is expected
