import pytest

from mitup_bot.models.base_model import BaseModel


class _ModelWithId(BaseModel):
    """Minimal concrete subclass with an explicit id attribute."""

    def __init__(self, id: int | None = None):
        self.id = id


def test_db_id_raises_value_error_when_id_is_none():
    model = _ModelWithId(id=None)

    with pytest.raises(ValueError, match="id is not set"):
        _ = model.db_id


def test_db_id_returns_id_when_set():
    model = _ModelWithId(id=42)

    assert model.db_id == 42  # 42 is the hardcoded expected value
