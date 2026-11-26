from pathlib import Path
import tomllib

import structlog.testing

from src.lib.utils.user_agent import PYPROJECT_PATH, get_user_agent, strip_user_agent


class TestGetUserAgent:
    def test_uses_product_name_and_project_version(self):
        with PYPROJECT_PATH.open("rb") as file:
            version = tomllib.load(file)["project"]["version"]

        assert get_user_agent() == f"ai-dial-scheduler/{version}"

    def test_falls_back_to_product_name_when_file_is_missing(self, tmp_path: Path):
        with structlog.testing.capture_logs() as logs:
            result = get_user_agent(tmp_path / "missing.toml")

        assert result == "ai-dial-scheduler"
        assert logs[0]["event"] == "user_agent_version_unavailable"

    def test_falls_back_to_product_name_when_version_is_absent(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "x"\n')

        assert get_user_agent(pyproject) == "ai-dial-scheduler"

    def test_falls_back_to_product_name_when_file_is_malformed(self, tmp_path: Path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("not = [valid")

        assert get_user_agent(pyproject) == "ai-dial-scheduler"


class TestStripUserAgent:
    def test_removes_user_agent_regardless_of_case(self):
        headers = {"User-Agent": "a", "user-agent": "b", "USER-AGENT": "c", "X-Custom": "keep"}

        assert strip_user_agent(headers) == {"X-Custom": "keep"}

    def test_returns_copy_and_leaves_input_untouched(self):
        headers = {"User-Agent": "a", "X-Custom": "keep"}

        result = strip_user_agent(headers)

        assert result is not headers
        assert headers == {"User-Agent": "a", "X-Custom": "keep"}

    def test_empty_headers(self):
        assert strip_user_agent({}) == {}
