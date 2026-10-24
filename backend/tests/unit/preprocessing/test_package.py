from candlestack import preprocessing


def test_root_has_every_name_of_its_public_api() -> None:
    """Other modules import preprocessing only through the package root, so ``__all__`` is the
    API."""
    assert [name for name in preprocessing.__all__ if not hasattr(preprocessing, name)] == []
