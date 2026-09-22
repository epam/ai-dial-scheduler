import os
from unittest.mock import MagicMock

import pytest

from src.lib.db import credentials
from src.lib.settings import DatabaseSettings


def _aws_settings(monkeypatch, region: str | None = "eu-central-1") -> DatabaseSettings:
    for name in ("DB_PASSWORD", "DB_URL", "DB_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DB_AUTH_MODE", "aws_iam")
    monkeypatch.setenv("DB_AWS_IAM_USER", "scheduler_iam")
    monkeypatch.setenv("DB_HOST", "pg.eu-central-1.rds.amazonaws.com")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "scheddb")
    if region is not None:
        monkeypatch.setenv("DB_AWS_REGION", region)
    return DatabaseSettings(_env_file=None)


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


class TestAwsRdsTokenProvider:
    def test_token_requested_for_configured_endpoint(self, monkeypatch, mocker):
        client = MagicMock()
        client.generate_db_auth_token.return_value = "rds-token"
        boto_client = mocker.patch.object(credentials.boto3, "client", return_value=client)

        provider = credentials.AwsRdsTokenProvider(_aws_settings(monkeypatch))
        token = provider.get_token_password()

        assert token == "rds-token"
        boto_client.assert_called_once_with("rds", region_name="eu-central-1")
        client.generate_db_auth_token.assert_called_once_with(
            DBHostname="pg.eu-central-1.rds.amazonaws.com",
            Port=5432,
            DBUsername="scheduler_iam",
            Region="eu-central-1",
        )

    def test_every_call_generates_a_fresh_token(self, monkeypatch, mocker):
        client = MagicMock()
        client.generate_db_auth_token.side_effect = ["first", "second"]
        mocker.patch.object(credentials.boto3, "client", return_value=client)

        provider = credentials.AwsRdsTokenProvider(_aws_settings(monkeypatch))

        assert provider.get_token_password() == "first"
        assert provider.get_token_password() == "second"

    def test_configured_region_wins_over_ambient(self, monkeypatch, mocker):
        boto_client = mocker.patch.object(credentials.boto3, "client", return_value=MagicMock())
        settings = _aws_settings(monkeypatch, region="eu-west-3")
        monkeypatch.setenv("AWS_REGION", "us-east-1")

        credentials.AwsRdsTokenProvider(settings)

        boto_client.assert_called_once_with("rds", region_name="eu-west-3")

    def test_aws_region_env_var_is_honoured(self, monkeypatch, mocker):
        # botocore's own chain only reads AWS_DEFAULT_REGION, but EKS/ECS/Lambda export AWS_REGION,
        # so the provider must read it directly. Exercised against the real botocore resolution.
        boto_client = mocker.patch.object(credentials.boto3, "client", return_value=MagicMock())
        settings = _aws_settings(monkeypatch, region=None)
        monkeypatch.setenv("AWS_REGION", "ap-south-1")

        credentials.AwsRdsTokenProvider(settings)

        boto_client.assert_called_once_with("rds", region_name="ap-south-1")

    def test_aws_default_region_env_var_is_honoured(self, monkeypatch, mocker):
        boto_client = mocker.patch.object(credentials.boto3, "client", return_value=MagicMock())
        settings = _aws_settings(monkeypatch, region=None)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-north-1")

        credentials.AwsRdsTokenProvider(settings)

        boto_client.assert_called_once_with("rds", region_name="eu-north-1")

    def test_unresolvable_region_fails_fast_naming_the_env_var(self, monkeypatch, mocker):
        boto_client = mocker.patch.object(credentials.boto3, "client", return_value=MagicMock())
        settings = _aws_settings(monkeypatch, region=None)
        # Neutralize the shared config file so the ambient developer profile cannot supply a region.
        monkeypatch.setenv("AWS_CONFIG_FILE", os.devnull)

        with pytest.raises(ValueError, match="DB_AWS_REGION"):
            credentials.AwsRdsTokenProvider(settings)

        boto_client.assert_not_called()

    def test_close_closes_client(self, monkeypatch, mocker):
        client = MagicMock()
        mocker.patch.object(credentials.boto3, "client", return_value=client)

        credentials.AwsRdsTokenProvider(_aws_settings(monkeypatch)).close()

        client.close.assert_called_once()


class TestAsyncAwsRdsTokenProvider:
    async def test_token_is_generated_off_the_event_loop(self, monkeypatch, mocker):
        client = MagicMock()
        client.generate_db_auth_token.return_value = "async-rds-token"
        mocker.patch.object(credentials.boto3, "client", return_value=client)
        to_thread = mocker.spy(credentials.asyncio, "to_thread")

        provider = credentials.AsyncAwsRdsTokenProvider(_aws_settings(monkeypatch))
        token = await provider.get_token_password()

        assert token == "async-rds-token"
        to_thread.assert_called_once()

    def test_close_closes_underlying_client(self, monkeypatch, mocker):
        client = MagicMock()
        mocker.patch.object(credentials.boto3, "client", return_value=client)

        credentials.AsyncAwsRdsTokenProvider(_aws_settings(monkeypatch)).close()

        client.close.assert_called_once()
