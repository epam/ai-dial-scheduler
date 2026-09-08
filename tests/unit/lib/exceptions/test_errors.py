import pytest

from src.lib.exceptions.errors import ScheduleRunError, SignInRequiredError
from src.lib.schemas.sign_in import SignInStatus


class TestScheduleRunError:
    def test_status_code_defaults_to_none(self):
        err = ScheduleRunError("boom", schedule_id="s1", run_id="r1")
        assert err.status_code is None
        assert err.schedule_id == "s1"
        assert err.run_id == "r1"

    def test_status_code_is_stored(self):
        err = ScheduleRunError("boom", schedule_id="s1", run_id="r1", status_code=502)
        assert err.status_code == 502


class TestSignInRequiredError:
    def test_exposes_service_id_and_state_without_parsing_message(self):
        status = SignInStatus(service_id="dial-native", state="not_signed_in", detail="sign in please")
        err = SignInRequiredError(status)

        assert err.service_id == "dial-native"
        assert err.state == "not_signed_in"
        assert err.detail == "sign in please"

    def test_carries_unknown_state_for_outages(self):
        status = SignInStatus(service_id="dial-native", state="unknown", detail="DIAL unreachable")
        err = SignInRequiredError(status)

        assert err.state == "unknown"

    def test_rejects_a_signed_in_status(self):
        """The invariant `state` narrows on: a signed-in status is never a reason to raise."""
        status = SignInStatus(service_id="dial-native", state="signed_in", detail="ok")

        with pytest.raises(ValueError):
            SignInRequiredError(status)
