"""Unit tests for the X-CONVERSATION-ID header helpers."""

import pytest

from src.lib.constants import CONVERSATION_ID_HEADER
from src.lib.utils.conversation_header import encode_header_value, with_conversation_id


def _is_printable_ascii(value: str) -> bool:
    return value.isascii() and all(0x20 <= ord(char) < 0x7F for char in value)


class TestEncodeHeaderValue:
    @pytest.mark.parametrize(
        "value",
        [
            "conversations/b/.scheduler/s/gpt-4__Daily report__r",
            "a b/c%d_e",
            "hi! how are you? ~`{}[]|\\\"'<>=+-*&^$#@;:,.",
            "",
        ],
    )
    def test_printable_ascii_is_unchanged(self, value):
        assert encode_header_value(value) == value

    def test_cyrillic_is_percent_encoded_and_space_kept(self):
        assert (
            encode_header_value("Щоденний звіт")
            == "%D0%A9%D0%BE%D0%B4%D0%B5%D0%BD%D0%BD%D0%B8%D0%B9 %D0%B7%D0%B2%D1%96%D1%82"
        )

    def test_emoji_is_percent_encoded_as_utf8_bytes(self):
        assert encode_header_value("📈") == "%F0%9F%93%88"

    def test_control_characters_are_percent_encoded(self):
        assert encode_header_value("a\nb\tc\x7f") == "a%0Ab%09c%7F"

    def test_literal_percent_escape_is_not_double_encoded(self):
        assert encode_header_value("50%25 done") == "50%25 done"

    @pytest.mark.parametrize("value", ["Щоденний звіт 📈", "line1\r\nline2", "\x00\x1f\x7f", "€ ok"])
    def test_result_is_always_printable_ascii(self, value):
        assert _is_printable_ascii(encode_header_value(value))

    @pytest.mark.parametrize("value", ["Щоденний звіт 📈", "a\nb", "plain"])
    def test_idempotent_on_its_own_output(self, value):
        once = encode_header_value(value)
        assert encode_header_value(once) == once


class TestWithConversationId:
    def test_none_returns_an_equal_copy_and_keeps_caller_header(self):
        headers = {"x-conversation-id": "manual", "X-Other": "1"}

        result = with_conversation_id(headers, None)

        assert result == headers
        assert result is not headers

    def test_adds_scheduler_header_and_keeps_other_headers(self):
        result = with_conversation_id({"X-Other": "1"}, "conversations/b/x")

        assert result == {"X-Other": "1", CONVERSATION_ID_HEADER: "conversations/b/x"}

    def test_caller_header_in_any_casing_is_replaced(self):
        headers = {"X-CONVERSATION-ID": "a", "x-conversation-id": "b", "X-Conversation-Id": "c", "X-Other": "1"}

        result = with_conversation_id(headers, "conversations/b/x")

        matching = [name for name in result if name.lower() == CONVERSATION_ID_HEADER.lower()]
        assert matching == [CONVERSATION_ID_HEADER]
        assert result[CONVERSATION_ID_HEADER] == "conversations/b/x"
        assert result["X-Other"] == "1"

    def test_value_is_encoded(self):
        result = with_conversation_id({}, "conversations/b/Щоденний звіт")

        assert result[CONVERSATION_ID_HEADER] == encode_header_value("conversations/b/Щоденний звіт")

    def test_input_is_not_mutated(self):
        headers = {"x-conversation-id": "manual"}

        with_conversation_id(headers, "conversations/b/x")

        assert headers == {"x-conversation-id": "manual"}
