from unittest.mock import MagicMock

from src.lib.db import credentials


class TestAzureTokenProvider:
    def test_get_token_password_requests_db_scope(self, mocker):
        fake_credential = MagicMock()
        fake_credential.get_token.return_value = MagicMock(token="the-token")
        ctor = mocker.patch.object(credentials, "DefaultAzureCredential", return_value=fake_credential)

        provider = credentials.AzureTokenProvider()
        token = provider.get_token_password()

        assert token == "the-token"
        fake_credential.get_token.assert_called_once_with(credentials.AZURE_DB_SCOPE)
        ctor.assert_called_once_with()

    def test_user_assigned_client_id_is_forwarded(self, mocker):
        ctor = mocker.patch.object(credentials, "DefaultAzureCredential", return_value=MagicMock())

        credentials.AzureTokenProvider(client_id="client-123")

        ctor.assert_called_once_with(managed_identity_client_id="client-123")

    def test_close_closes_credential(self, mocker):
        fake_credential = MagicMock()
        mocker.patch.object(credentials, "DefaultAzureCredential", return_value=fake_credential)

        provider = credentials.AzureTokenProvider()
        provider.close()

        fake_credential.close.assert_called_once()


class TestAsyncAzureTokenProvider:
    async def test_get_token_password_requests_db_scope(self, mocker):
        fake_credential = MagicMock()

        async def _get_token(scope):
            assert scope == credentials.AZURE_DB_SCOPE
            return MagicMock(token="async-token")

        fake_credential.get_token = _get_token
        mocker.patch.object(credentials, "AsyncDefaultAzureCredential", return_value=fake_credential)

        provider = credentials.AsyncAzureTokenProvider()
        token = await provider.get_token_password()

        assert token == "async-token"


class TestGcpHelpers:
    def test_gcp_ip_type_private(self):
        from google.cloud.sql.connector import IPTypes

        assert credentials.gcp_ip_type(private=True) == IPTypes.PRIVATE
        assert credentials.gcp_ip_type(private=False) == IPTypes.PUBLIC

    def test_create_gcp_connector_uses_lazy_refresh(self, mocker):
        connector_ctor = mocker.patch.object(credentials, "Connector", return_value=MagicMock())

        credentials.create_gcp_connector()

        _, kwargs = connector_ctor.call_args
        assert kwargs.get("refresh_strategy") == "LAZY"
