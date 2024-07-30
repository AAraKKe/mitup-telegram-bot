from mitup_bot.monitoring import NULL_DIMENSIONALITY, Dimensionality


def test_dimensionality_dimensions_are_consistent():
    dim1 = Dimensionality(a="dima", b="dimb")
    dim2 = Dimensionality(b="dimb", a="dima")

    assert dim1 == dim2
    assert list(dim1.dimensions.values()) == list(dim2.dimensions.values())


def test_dimensionality_str():
    dim = Dimensionality(a="dima", b="dimb")
    assert str(dim) == "{'a': 'dima', 'b': 'dimb'}"


def test_dimensionality_repr():
    dim = Dimensionality(a="dima", b="dimb")
    assert repr(dim) == f"{{'a': 'dima', 'b': 'dimb'}} [hash: {hash(dim)}]"


def test_null_dimensionality():
    assert Dimensionality.or_null(None) == NULL_DIMENSIONALITY


def test_or_null_provides_dimensionality():
    assert Dimensionality.or_null({"a": "dima", "b": "dimb"}) == Dimensionality(a="dima", b="dimb")
