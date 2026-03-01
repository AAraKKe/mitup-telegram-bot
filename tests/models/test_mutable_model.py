import pytest
from pydantic import ValidationError

from mitup_bot.models.mutable_model import MutableModel


class SampleModel(MutableModel):
    name: str
    age: int = 0


# --- __setattr__ ---


def test_setattr_propagates_change_tracking():
    """Verify that assigning a field on a MutableModel instance calls changed().

    patch.object is not used here because Pydantic v2's ModelMetaclass intercepts
    class-level setattr, making mock injection unreliable for both the class and
    the instance. Instead, a local subclass overrides changed() with a plain
    call counter, which works correctly through the normal MRO without triggering
    Pydantic's attribute machinery.
    """
    call_count = 0

    class _TrackingModel(SampleModel):
        def changed(self) -> None:  # type: ignore[override]
            nonlocal call_count
            call_count += 1

    model = _TrackingModel(name="Alice")
    call_count = 0  # reset — __init__ may trigger changed() during field setup

    model.name = "Bob"

    assert call_count == 1


# --- coerce ---


def test_coerce_returns_same_instance_when_already_correct_type():
    model = SampleModel(name="Alice")

    result = SampleModel.coerce("key", model)

    assert result is model


def test_coerce_returns_none_for_none():
    result = SampleModel.coerce("key", None)

    assert result is None


def test_coerce_deserializes_json_string():
    json_str = '{"name": "Alice", "age": 30}'

    result = SampleModel.coerce("key", json_str)

    assert isinstance(result, SampleModel)
    assert result.name == "Alice"
    assert result.age == 30


def test_coerce_constructs_from_dict():
    data = {"name": "Bob", "age": 42}

    result = SampleModel.coerce("key", data)

    assert isinstance(result, SampleModel)
    assert result.name == "Bob"
    assert result.age == 42


def test_coerce_raises_for_invalid_json():
    with pytest.raises(ValidationError):
        SampleModel.coerce("key", "{not valid json}")


def test_coerce_delegates_to_super_for_unsupported_type():
    with pytest.raises(ValueError, match="does not accept objects of type"):
        SampleModel.coerce("key", 12345)
