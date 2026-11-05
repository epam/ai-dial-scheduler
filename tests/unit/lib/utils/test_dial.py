"""Unit tests for shared DIAL URL/token utilities."""

from src.lib.utils.dial import strip_bearer_prefix


class TestStripBearerPrefix:
    def test_strips_bearer_prefix(self):
        assert strip_bearer_prefix("Bearer my-token") == "my-token"

    def test_passthrough_without_prefix(self):
        assert strip_bearer_prefix("raw-api-key") == "raw-api-key"

    def test_empty_string(self):
        assert strip_bearer_prefix("") == ""

    def test_bearer_only(self):
        assert strip_bearer_prefix("Bearer ") == ""

    def test_case_sensitive(self):
        assert strip_bearer_prefix("bearer my-token") == "bearer my-token"
