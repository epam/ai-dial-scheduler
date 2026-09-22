from typing import Any, cast

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from src.lib.db.credentials import (
    AwsRdsTokenProvider,
    AzureTokenProvider,
    SyncTokenProvider,
    create_gcp_connector,
    gcp_ip_type,
)
from src.lib.db.enums import DbAuthMode, DbDriver
from src.lib.db.schema import attach_schema_listener
from src.lib.db.url import build_aws_iam_sync_url, build_azure_entra_sync_url, build_database_url
from src.lib.settings import DatabaseSettings
from src.lib.utils.logging import logger


def build_sync_engine(db_settings: DatabaseSettings) -> Engine:
    """Build a synchronous SQLAlchemy engine for the selected auth mode.

    Used by the APScheduler ``SQLAlchemyJobStore`` and Alembic offline paths. Each mode keeps the
    existing pooling behavior; workload-identity modes inject short-lived credentials at connect
    time rather than embedding a password in the URL. When ``db_schema`` is set, a ``connect``
    event listener is attached to set ``search_path`` on every new DBAPI connection.
    """
    mode = db_settings.db_auth_mode
    if mode in (DbAuthMode.PASSWORD, DbAuthMode.CONNECTION_STRING):
        engine = _build_url_engine(db_settings)
    elif mode == DbAuthMode.GCP_IAM:
        engine = _build_gcp_engine(db_settings)
    elif mode == DbAuthMode.AZURE_ENTRA:
        engine = _build_azure_engine(db_settings)
    elif mode == DbAuthMode.AWS_IAM:
        engine = _build_aws_engine(db_settings)
    else:
        raise ValueError(f"Unsupported DB auth mode for sync engine: {mode}")

    if db_settings.db_schema is not None:
        attach_schema_listener(engine, db_settings.db_schema)

    return engine


def _common_engine_kwargs(db_settings: DatabaseSettings) -> dict[str, Any]:
    return {"echo": db_settings.db_echo, "pool_pre_ping": db_settings.db_pool_pre_ping}


def _build_url_engine(db_settings: DatabaseSettings) -> Engine:
    url = build_database_url(db_settings, driver=DbDriver.SYNC)
    return create_engine(url, **_common_engine_kwargs(db_settings))


def _build_gcp_engine(db_settings: DatabaseSettings) -> Engine:
    connector = create_gcp_connector()
    ip_type = gcp_ip_type(private=db_settings.db_gcp_private_ip)
    # Validated as non-None for gcp_iam mode by DatabaseSettings.validate_auth_requirements.
    instance_connection_name = cast(str, db_settings.db_gcp_instance_connection_name)

    def getconn() -> Any:
        try:
            return connector.connect(
                instance_connection_name,
                "pg8000",
                user=db_settings.db_gcp_iam_user,
                db=db_settings.db_name,
                ip_type=ip_type,
                enable_iam_auth=True,
            )
        except Exception as exc:
            # Log mode + exception type only — never the exception args, which could carry secrets.
            logger.error(
                "DB credential acquisition failed.",
                mode=DbAuthMode.GCP_IAM.value,
                error_type=type(exc).__name__,
            )
            raise

    engine = create_engine("postgresql+pg8000://", creator=getconn, **_common_engine_kwargs(db_settings))

    @event.listens_for(engine, "engine_disposed")
    def _close_connector(_engine: Any) -> None:
        connector.close()

    logger.info("Building sync DB engine.", mode=DbAuthMode.GCP_IAM.value)
    return engine


def _inject_token_password(provider: SyncTokenProvider, cparams: dict[str, Any], mode: DbAuthMode) -> None:
    """Inject a fresh short-lived token as the connection password for a new DBAPI connection."""
    try:
        cparams["password"] = provider.get_token_password()
    except Exception as exc:
        # Log mode + exception type only — never the exception args, which could carry secrets.
        logger.error(
            "DB credential acquisition failed.",
            mode=mode.value,
            error_type=type(exc).__name__,
        )
        raise


def _attach_token_password_listeners(engine: Engine, provider: SyncTokenProvider, mode: DbAuthMode) -> None:
    """Wire per-connection token injection and provider teardown onto a passwordless engine."""

    @event.listens_for(engine, "do_connect")
    def _provide_token(_dialect: Any, _conn_rec: Any, _cargs: Any, cparams: dict[str, Any]) -> None:
        _inject_token_password(provider, cparams, mode)

    @event.listens_for(engine, "engine_disposed")
    def _close_provider(_engine: Any) -> None:
        provider.close()


def _build_token_password_engine(db_settings: DatabaseSettings, url: str) -> Engine:
    """Create a passwordless psycopg2 engine that receives a fresh token per connection.

    Managed PostgreSQL offerings mandate TLS; require it on the client rather than relying on
    psycopg2's default 'prefer' (which would silently allow a non-TLS fallback). This mirrors the
    async path's ssl="require".
    """
    return create_engine(url, connect_args={"sslmode": "require"}, **_common_engine_kwargs(db_settings))


def _build_azure_engine(db_settings: DatabaseSettings) -> Engine:
    provider = AzureTokenProvider(client_id=db_settings.db_azure_managed_identity_client_id)
    engine = _build_token_password_engine(db_settings, build_azure_entra_sync_url(db_settings))
    _attach_token_password_listeners(engine, provider, DbAuthMode.AZURE_ENTRA)

    logger.info("Building sync DB engine.", mode=DbAuthMode.AZURE_ENTRA.value)
    return engine


def _build_aws_engine(db_settings: DatabaseSettings) -> Engine:
    # Constructing the provider resolves the AWS region eagerly, so a misconfigured region fails
    # here at engine build time rather than on the first connection attempt.
    provider = AwsRdsTokenProvider(db_settings)
    engine = _build_token_password_engine(db_settings, build_aws_iam_sync_url(db_settings))
    _attach_token_password_listeners(engine, provider, DbAuthMode.AWS_IAM)

    logger.info("Building sync DB engine.", mode=DbAuthMode.AWS_IAM.value)
    return engine
