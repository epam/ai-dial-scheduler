from starlette.requests import Request

from src.api.dependencies.metadata_filter import parse_metadata_filter


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


class TestParseMetadataFilter:
    def test_no_params_returns_empty(self):
        assert parse_metadata_filter(make_request("")) == {}

    def test_ignores_non_prefixed_params(self):
        result = parse_metadata_filter(make_request("limit=10&offset=0"))
        assert result == {}

    def test_single_metadata_param(self):
        result = parse_metadata_filter(make_request("metadata__tenant=hcscd"))
        assert result == {"tenant": "hcscd"}

    def test_multiple_metadata_params(self):
        result = parse_metadata_filter(make_request("metadata__tenant=hcscd&metadata__env=prod"))
        assert result == {"tenant": "hcscd", "env": "prod"}

    def test_mixes_metadata_and_regular_params(self):
        result = parse_metadata_filter(make_request("limit=5&metadata__tenant=hcscd&offset=0"))
        assert result == {"tenant": "hcscd"}

    def test_key_containing_double_underscore(self):
        result = parse_metadata_filter(make_request("metadata__cost__center=eng"))
        assert result == {"cost__center": "eng"}

    def test_duplicate_key_last_wins(self):
        result = parse_metadata_filter(make_request("metadata__tenant=first&metadata__tenant=second"))
        assert result == {"tenant": "second"}

    def test_empty_value_is_preserved(self):
        result = parse_metadata_filter(make_request("metadata__tenant="))
        assert result == {"tenant": ""}

    def test_empty_key_is_skipped(self):
        result = parse_metadata_filter(make_request("metadata__=value"))
        assert result == {}
