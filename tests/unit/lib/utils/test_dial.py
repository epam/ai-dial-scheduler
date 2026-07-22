"""Unit tests for shared DIAL URL/token utilities."""

import pytest

from src.lib.utils.dial import (
    extract_deployment_id,
    parse_deployment,
    strip_bearer_prefix,
)


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


class TestExtractDeploymentId:
    def test_model_deployment(self):
        url = "https://dial.example.com/openai/deployments/gpt-4o/chat/completions"
        assert extract_deployment_id(url) == "gpt-4o"

    def test_application_deployment_multi_segment(self):
        url = "https://dial.example.com/openai/deployments/applications/bkt/My App 1.0/chat/completions"
        assert extract_deployment_id(url) == "applications/bkt/My App 1.0"

    def test_url_without_deployments_raises(self):
        with pytest.raises(ValueError, match="cannot extract deployment"):
            extract_deployment_id("https://dial.example.com/v1/chat/completions")

    def test_empty_deployment_segment_raises(self):
        with pytest.raises(ValueError, match="cannot extract deployment"):
            extract_deployment_id("https://dial.example.com/openai/deployments//chat/completions")

    def test_no_trailing_chat_completions(self):
        url = "https://dial.example.com/openai/deployments/gpt-4o"
        assert extract_deployment_id(url) == "gpt-4o"


class TestParseDeployment:
    def test_model_deployment_chat_completions(self):
        url = "https://dial.example.com/openai/deployments/gpt-4o/chat/completions"
        assert parse_deployment(url) == ("https://dial.example.com", "gpt-4o")

    def test_application_deployment_multi_segment_preserved(self):
        url = "https://dial.example.com/openai/deployments/applications/bkt/My App/chat/completions"
        assert parse_deployment(url) == ("https://dial.example.com", "applications/bkt/My App")

    def test_url_without_deployments_raises(self):
        with pytest.raises(ValueError, match="cannot extract deployment"):
            parse_deployment("https://dial.example.com/v1/chat/completions")
