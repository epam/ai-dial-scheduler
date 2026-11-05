from unittest.mock import AsyncMock

import pytest
import structlog.testing

from src.lib.exceptions.errors import DialServiceError, SignInRequiredError
from src.lib.schemas.sign_in import ExternalServiceAuthStatus
from src.lib.services.sign_in_service import SignInService


TEST_USER_ID = "user-1"
TEST_SERVICE_ID = "dial-native"
TEST_APP_ID = "public/dial_scheduler__1.0.0"
TEST_API_KEY = "caller-key"
TEST_TIMEOUT = 120.0


def make_service(dial_client) -> SignInService:
    return SignInService(dial_client, scheduler_app_id=TEST_APP_ID, signin_timeout_seconds=TEST_TIMEOUT)


def signed_in_status() -> ExternalServiceAuthStatus:
    return ExternalServiceAuthStatus(user_level_auth_status="SIGNED_IN", app_level_auth_status="SIGNED_IN")


def not_signed_in_status() -> ExternalServiceAuthStatus:
    return ExternalServiceAuthStatus(user_level_auth_status="SIGNED_OUT", app_level_auth_status="SIGNED_IN")


def no_admin_consent_status() -> ExternalServiceAuthStatus:
    return ExternalServiceAuthStatus(user_level_auth_status="SIGNED_IN", app_level_auth_status="SIGNED_OUT")


class TestSignInServiceCheck:
    async def test_signed_in_when_both_statuses_signed_in(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = signed_in_status()
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert status.state == "signed_in"
        assert status.signed_in is True
        assert status.service_id == TEST_SERVICE_ID

    async def test_not_signed_in_when_user_level_signed_out(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = not_signed_in_status()
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert status.state == "not_signed_in"
        assert status.signed_in is False

    async def test_unknown_when_app_level_not_signed_in(self):
        """Admin consent missing — signing in would not fix this, so it is not `not_signed_in`."""
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = no_admin_consent_status()
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert status.state == "unknown"
        assert "sign in" not in status.detail.lower()

    async def test_unknown_on_dial_service_error(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.side_effect = DialServiceError("boom")
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert status.state == "unknown"

    async def test_unknown_logs_the_reason(self):
        """An unknown outcome must be diagnosable from logs alone."""
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.side_effect = DialServiceError("not found")
        service = make_service(dial_client)

        with structlog.testing.capture_logs() as logs:
            await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        failures = [log for log in logs if log.get("event") == "sign_in_check_failed"]
        assert len(failures) == 1
        assert failures[0]["log_level"] == "warning"

    async def test_check_calls_client_with_caller_api_key_and_app_id(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = signed_in_status()
        service = make_service(dial_client)

        await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        dial_client.get_external_service_auth_status.assert_awaited_once_with(
            TEST_API_KEY, TEST_APP_ID, TEST_SERVICE_ID
        )


class TestSignInServiceChannelInteract:
    async def test_not_signed_in_with_channel_triggers_interact(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = not_signed_in_status()
        dial_client.request_external_service_signin.return_value = False
        service = make_service(dial_client)

        await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY, channel_id="chan-1")

        dial_client.request_external_service_signin.assert_awaited_once_with(
            TEST_API_KEY, "chan-1", f"applications/{TEST_APP_ID}/external_services/{TEST_SERVICE_ID}", TEST_TIMEOUT
        )

    async def test_interact_success_re_probes_and_reports_signed_in(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.side_effect = [not_signed_in_status(), signed_in_status()]
        dial_client.request_external_service_signin.return_value = True
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY, channel_id="chan-1")

        assert status.state == "signed_in"
        assert dial_client.get_external_service_auth_status.await_count == 2

    async def test_interact_denied_keeps_not_signed_in(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = not_signed_in_status()
        dial_client.request_external_service_signin.return_value = False
        service = make_service(dial_client)

        status = await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY, channel_id="chan-1")

        assert status.state == "not_signed_in"
        assert dial_client.get_external_service_auth_status.await_count == 1

    async def test_no_channel_id_skips_interact(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = not_signed_in_status()
        service = make_service(dial_client)

        await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        dial_client.request_external_service_signin.assert_not_awaited()

    async def test_already_signed_in_skips_interact(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = signed_in_status()
        service = make_service(dial_client)

        await service.check(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY, channel_id="chan-1")

        dial_client.request_external_service_signin.assert_not_awaited()


class TestSignInServiceRequire:
    async def test_require_passes_when_signed_in(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = signed_in_status()
        service = make_service(dial_client)

        await service.require(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

    async def test_require_raises_not_signed_in(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.return_value = not_signed_in_status()
        service = make_service(dial_client)

        with pytest.raises(SignInRequiredError) as exc_info:
            await service.require(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert exc_info.value.state == "not_signed_in"
        assert exc_info.value.service_id == TEST_SERVICE_ID

    async def test_require_raises_unknown_on_outage(self):
        dial_client = AsyncMock()
        dial_client.get_external_service_auth_status.side_effect = DialServiceError("timeout")
        service = make_service(dial_client)

        with pytest.raises(SignInRequiredError) as exc_info:
            await service.require(TEST_USER_ID, TEST_SERVICE_ID, TEST_API_KEY)

        assert exc_info.value.state == "unknown"
