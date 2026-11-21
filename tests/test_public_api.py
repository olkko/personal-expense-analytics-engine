import personal_expense_analytics_engine as package


def test_every_name_in_all_is_importable():
    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert missing == []


def test_all_has_no_duplicates():
    assert len(package.__all__) == len(set(package.__all__))
