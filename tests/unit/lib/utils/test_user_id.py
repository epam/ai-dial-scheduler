"""Unit tests for extract_user_id — happy-path traversal (US1) and full fallback/skip rules (US3)."""

from src.lib.utils.user_id import extract_user_id


class TestExtractUserIdHappyPath:
    def test_first_path_resolves(self):
        data = {"userClaims": {"mail": ["alice@example.com"]}}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "alice@example.com"

    def test_single_key_path(self):
        data = {"project": "proj-alpha"}
        result = extract_user_id(data, ["project"])
        assert result == "proj-alpha"

    def test_returns_stripped_string(self):
        data = {"userClaims": {"mail": ["  bob@example.com  "]}}
        result = extract_user_id(data, ["userClaims.mail.0"])
        assert result == "bob@example.com"


class TestExtractUserIdFallback:
    def test_falls_back_to_second_path_when_first_absent(self):
        data = {"project": "proj-beta"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "proj-beta"

    def test_returns_none_when_no_path_resolves(self):
        data = {"other": "value"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result is None

    def test_empty_path_list_returns_none(self):
        data = {"project": "alpha"}
        result = extract_user_id(data, [])
        assert result is None

    def test_empty_data_returns_none(self):
        result = extract_user_id({}, ["userClaims.mail.0", "project"])
        assert result is None


class TestExtractUserIdSkipRules:
    def test_skips_empty_string_value(self):
        data = {"userClaims": {"mail": [""]}, "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_skips_whitespace_only_value(self):
        data = {"userClaims": {"mail": ["   "]}, "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_skips_list_index_out_of_range(self):
        data = {"userClaims": {"mail": []}, "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_skips_dict_key_missing(self):
        data = {"userClaims": {}, "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_skips_when_segment_expects_list_but_finds_dict(self):
        data = {"userClaims": {"mail": {"0": "not-a-list"}}, "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_skips_when_segment_expects_dict_but_finds_list(self):
        data = {"userClaims": ["unexpected"], "project": "fallback"}
        result = extract_user_id(data, ["userClaims.mail.0", "project"])
        assert result == "fallback"

    def test_digit_segment_treated_as_list_index(self):
        data = {"items": ["first", "second", "third"]}
        result = extract_user_id(data, ["items.1"])
        assert result == "second"

    def test_type_mismatch_non_string_final_value_coerced(self):
        data = {"count": 42}
        result = extract_user_id(data, ["count"])
        assert result == "42"

    def test_false_value_coerced_to_non_empty_string(self):
        data = {"flag": False}
        result = extract_user_id(data, ["flag"])
        assert result == "False"
