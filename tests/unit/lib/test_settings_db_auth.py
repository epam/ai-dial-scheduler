from pydantic import ValidationError
import pytest

from src.lib.db.enums import DbAuthMode
from src.lib.settings import DatabaseSettings


_GCP_ENV = ("DB_GCP_INSTANCE_CONNECTION_NAME", "DB_GCP_IAM_USER", "DB_GCP_PRIVATE_IP")
_AZURE_ENV = ("DB_AZURE_ENTRA_USER", "DB_AZURE_MANAGED_IDENTITY_CLIENT_ID")


@pytest.fixture
def clean_db_env(monkeypatch):
    """Remove every DB_* override so each test controls exactly what is set."""
    for name in (
        "DB_AUTH_MODE",
        "DB_PASSWORD",
        "DB_URL",
        "DB_SCHEMA",
        *_GCP_ENV,
        *_AZURE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestDefaultMode:
    def test_unset_auth_mode_defaults_to_password(self, clean_db_env):
        clean_db_env.setenv("DB_PASSWORD", "secret")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_auth_mode == DbAuthMode.PASSWORD


class TestGcpIamValidation:
    def test_missing_gcp_fields_fail_fast_naming_them(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "gcp_iam")
        with pytest.raises(ValidationError) as exc:
            DatabaseSettings(_env_file=None)
        message = str(exc.value)
        assert "db_gcp_instance_connection_name" in message
        assert "db_gcp_iam_user" in message

    def test_valid_gcp_config_constructs(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "gcp_iam")
        clean_db_env.setenv("DB_GCP_INSTANCE_CONNECTION_NAME", "proj:region:inst")
        clean_db_env.setenv("DB_GCP_IAM_USER", "svc@proj.iam")
        clean_db_env.setenv("DB_GCP_PRIVATE_IP", "true")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_auth_mode == DbAuthMode.GCP_IAM
        assert settings.db_gcp_instance_connection_name == "proj:region:inst"
        assert settings.db_gcp_iam_user == "svc@proj.iam"
        assert settings.db_gcp_private_ip is True


class TestAzureEntraValidation:
    def test_missing_azure_user_fails_fast_naming_it(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "azure_entra")
        with pytest.raises(ValidationError) as exc:
            DatabaseSettings(_env_file=None)
        assert "db_azure_entra_user" in str(exc.value)

    def test_valid_azure_config_constructs(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "azure_entra")
        clean_db_env.setenv("DB_AZURE_ENTRA_USER", "scheduler-identity")
        clean_db_env.setenv("DB_AZURE_MANAGED_IDENTITY_CLIENT_ID", "client-123")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_auth_mode == DbAuthMode.AZURE_ENTRA
        assert settings.db_azure_entra_user == "scheduler-identity"
        assert settings.db_azure_managed_identity_client_id == "client-123"

    def test_azure_user_optional_client_id_defaults_none(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "azure_entra")
        clean_db_env.setenv("DB_AZURE_ENTRA_USER", "scheduler-identity")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_azure_managed_identity_client_id is None


class TestContradictoryConfigRejected:
    def test_password_set_under_gcp_iam_is_rejected(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "gcp_iam")
        clean_db_env.setenv("DB_GCP_INSTANCE_CONNECTION_NAME", "proj:region:inst")
        clean_db_env.setenv("DB_GCP_IAM_USER", "svc@proj.iam")
        clean_db_env.setenv("DB_PASSWORD", "should-not-be-here")
        with pytest.raises(ValidationError) as exc:
            DatabaseSettings(_env_file=None)
        message = str(exc.value)
        assert "must not set" in message
        assert "db_password" in message

    def test_password_set_under_azure_entra_is_rejected(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "azure_entra")
        clean_db_env.setenv("DB_AZURE_ENTRA_USER", "identity")
        clean_db_env.setenv("DB_PASSWORD", "should-not-be-here")
        with pytest.raises(ValidationError) as exc:
            DatabaseSettings(_env_file=None)
        assert "db_password" in str(exc.value)

    def test_url_set_under_gcp_iam_is_rejected(self, clean_db_env):
        clean_db_env.setenv("DB_AUTH_MODE", "gcp_iam")
        clean_db_env.setenv("DB_GCP_INSTANCE_CONNECTION_NAME", "proj:region:inst")
        clean_db_env.setenv("DB_GCP_IAM_USER", "svc@proj.iam")
        clean_db_env.setenv("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
        with pytest.raises(ValidationError) as exc:
            DatabaseSettings(_env_file=None)
        assert "db_url" in str(exc.value)


class TestDbSchema:
    def test_schema_defaults_to_none(self, clean_db_env):
        clean_db_env.setenv("DB_PASSWORD", "secret")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_schema is None

    def test_valid_schema_is_accepted(self, clean_db_env):
        clean_db_env.setenv("DB_PASSWORD", "secret")
        clean_db_env.setenv("DB_SCHEMA", "myschema")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_schema == "myschema"

    def test_schema_with_underscores_and_digits_is_accepted(self, clean_db_env):
        clean_db_env.setenv("DB_PASSWORD", "secret")
        clean_db_env.setenv("DB_SCHEMA", "_my_schema_2")
        settings = DatabaseSettings(_env_file=None)
        assert settings.db_schema == "_my_schema_2"
