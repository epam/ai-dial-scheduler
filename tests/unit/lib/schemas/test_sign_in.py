from pydantic import ValidationError
import pytest

from src.lib.schemas.sign_in import SignInStatus


class TestSignInStatus:
    def test_signed_in_derives_true(self):
        status = SignInStatus(service_id="dial-native", state="signed_in", detail="ok")
        assert status.signed_in is True

    def test_not_signed_in_derives_false(self):
        status = SignInStatus(service_id="dial-native", state="not_signed_in", detail="nope")
        assert status.signed_in is False

    def test_unknown_derives_false(self):
        status = SignInStatus(service_id="dial-native", state="unknown", detail="outage")
        assert status.signed_in is False

    def test_invalid_state_rejected(self):
        with pytest.raises(ValidationError):
            SignInStatus(service_id="dial-native", state="bogus", detail="x")

    def test_whitespace_only_service_id_rejected(self):
        with pytest.raises(ValidationError):
            SignInStatus(service_id="   ", state="signed_in", detail="x")

    def test_required_fields_and_state_enum_in_json_schema(self):
        schema = SignInStatus.model_json_schema()
        assert set(schema["required"]) >= {"service_id", "state"}
        state_schema = schema["properties"]["state"]
        assert set(state_schema.get("enum", [])) == {"signed_in", "not_signed_in", "unknown"}
