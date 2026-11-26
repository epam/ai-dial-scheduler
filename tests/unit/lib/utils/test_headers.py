from src.lib.utils.headers import without_headers


def test_drops_matching_names_regardless_of_case():
    headers = {"Authorization": "a", "authorization": "b", "X-Keep": "1"}

    assert without_headers(headers, ("AUTHORIZATION",)) == {"X-Keep": "1"}


def test_drops_every_listed_name():
    headers = {"api-key": "a", "User-Agent": "b", "X-Keep": "1"}

    assert without_headers(headers, ("Api-Key", "user-agent")) == {"X-Keep": "1"}


def test_returns_a_copy_and_leaves_the_input_untouched():
    headers = {"Authorization": "a", "X-Keep": "1"}

    result = without_headers(headers, ("authorization",))

    assert result is not headers
    assert headers == {"Authorization": "a", "X-Keep": "1"}


def test_no_names_returns_an_equal_copy():
    headers = {"X-Keep": "1"}

    result = without_headers(headers, ())

    assert result == headers
    assert result is not headers
