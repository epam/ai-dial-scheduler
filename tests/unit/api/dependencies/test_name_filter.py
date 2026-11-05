from starlette.requests import Request

from src.api.dependencies.name_filter import parse_name_filter


def make_request(query_string: str) -> Request:
    """Build a minimal Starlette Request carrying the given raw query string."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/schedules/",
        "query_string": query_string.encode("utf-8"),
        "headers": [],
    }
    return Request(scope)


class TestParseNameFilter:
    def test_no_param_returns_none(self):
        assert parse_name_filter(make_request("")) is None

    def test_ignores_other_params(self):
        assert parse_name_filter(make_request("limit=10&offset=0")) is None

    def test_returns_term_when_present(self):
        assert parse_name_filter(make_request("name=report")) == "report"

    def test_empty_value_returns_none(self):
        assert parse_name_filter(make_request("name=")) is None

    def test_whitespace_only_returns_none(self):
        assert parse_name_filter(make_request("name=%20%20")) is None

    def test_preserves_internal_whitespace(self):
        assert parse_name_filter(make_request("name=daily%20report")) == "daily report"

    def test_last_value_wins_on_duplicates(self):
        assert parse_name_filter(make_request("name=first&name=second")) == "second"
