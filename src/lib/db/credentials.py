import asyncio
import os
from typing import Protocol, cast

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
import boto3
from google.cloud.sql.connector import Connector, IPTypes

from src.lib.settings import DatabaseSettings


# Microsoft Entra OAuth scope that grants access to Azure Database for PostgreSQL.
AZURE_DB_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def gcp_ip_type(*, private: bool) -> IPTypes:
    """Map the ``db_gcp_private_ip`` flag to a Cloud SQL Connector IP type.

    Only PUBLIC and PRIVATE IP are supported via the boolean flag; PSC (Private Service Connect)
    is intentionally out of scope (YAGNI).
    """
    return IPTypes.PRIVATE if private else IPTypes.PUBLIC


def create_gcp_connector(*, loop: asyncio.AbstractEventLoop | None = None) -> Connector:
    """Create a Cloud SQL Connector.

    When ``loop`` is provided the connector is bound to it for async (asyncpg) usage so that
    ``connect_async`` always runs on its originating loop. Without a loop the connector is used
    synchronously (pg8000) for the APScheduler job store and Alembic.
    """
    if loop is not None:
        return Connector(loop=loop, refresh_strategy="LAZY")
    return Connector(refresh_strategy="LAZY")


class AzureTokenProvider:
    """Synchronous Microsoft Entra token provider for psycopg2 connections.

    ``DefaultAzureCredential`` resolves the workload's managed identity in-cluster and caches
    tokens internally, refreshing only when near expiry, so fetching per connection is cheap.
    """

    def __init__(self, client_id: str | None = None) -> None:
        if client_id:
            self._credential = DefaultAzureCredential(managed_identity_client_id=client_id)
        else:
            self._credential = DefaultAzureCredential()

    def get_token_password(self) -> str:
        """Return a valid Entra access token to be used as the connection password."""
        return self._credential.get_token(AZURE_DB_SCOPE).token

    def close(self) -> None:
        self._credential.close()


class AsyncAzureTokenProvider:
    """Asynchronous Microsoft Entra token provider for asyncpg connections."""

    def __init__(self, client_id: str | None = None) -> None:
        if client_id:
            self._credential = AsyncDefaultAzureCredential(managed_identity_client_id=client_id)
        else:
            self._credential = AsyncDefaultAzureCredential()

    async def get_token_password(self) -> str:
        """Return a valid Entra access token to be used as the connection password."""
        token = await self._credential.get_token(AZURE_DB_SCOPE)
        return token.token

    async def close(self) -> None:
        await self._credential.close()


class SyncTokenProvider(Protocol):
    """Sync provider of a short-lived token used as the DB connection password."""

    def get_token_password(self) -> str: ...

    def close(self) -> None: ...


class AsyncTokenProvider(Protocol):
    """Async provider of a short-lived token used as the DB connection password.

    Only token retrieval is part of the protocol: teardown differs per provider (the Azure
    credential closes asynchronously, the boto3 client synchronously) and is handled by the
    engine's own release helpers.
    """

    async def get_token_password(self) -> str: ...


def _resolve_aws_region(configured: str | None) -> str:
    """Resolve the AWS region used to sign RDS IAM tokens.

    Precedence follows the AWS SDK convention: an explicitly configured region, then the
    ``AWS_REGION`` environment variable, then whatever botocore itself resolves
    (``AWS_DEFAULT_REGION`` and the shared config file). ``AWS_REGION`` must be read directly
    because botocore's own chain only honours ``AWS_DEFAULT_REGION``, while EKS/ECS/Lambda export
    ``AWS_REGION``. Instance metadata is not consulted. A region is mandatory for SigV4 signing,
    so failing to resolve one is a configuration error.
    """
    if configured:
        return configured

    region = os.environ.get("AWS_REGION") or boto3.session.Session().region_name
    if not region:
        raise ValueError("AWS region could not be resolved from the environment; set DB_AWS_REGION.")
    return region


class AwsRdsTokenProvider:
    """Synchronous AWS RDS IAM token provider for psycopg2 connections.

    ``generate_db_auth_token`` builds a SigV4-presigned token locally, valid for 15 minutes; the
    token is generated per connection rather than cached, so pooled connections are never handed
    an expired credential. Resolving the underlying AWS credentials (IRSA, instance metadata, STS)
    may perform I/O, which is why the async wrapper offloads this to a thread.
    """

    def __init__(self, db_settings: DatabaseSettings) -> None:
        self._host = db_settings.db_host
        self._port = db_settings.db_port
        # Validated as non-None for aws_iam mode by DatabaseSettings.validate_auth_requirements.
        self._user = cast(str, db_settings.db_aws_iam_user)
        self._region = _resolve_aws_region(db_settings.db_aws_region)
        self._client = boto3.client("rds", region_name=self._region)

    def get_token_password(self) -> str:
        """Return a freshly signed IAM auth token to be used as the connection password."""
        return self._client.generate_db_auth_token(
            DBHostname=self._host,
            Port=self._port,
            DBUsername=self._user,
            Region=self._region,
        )

    def close(self) -> None:
        self._client.close()


class AsyncAwsRdsTokenProvider:
    """Asynchronous AWS RDS IAM token provider for asyncpg connections."""

    def __init__(self, db_settings: DatabaseSettings) -> None:
        self._provider = AwsRdsTokenProvider(db_settings)

    async def get_token_password(self) -> str:
        """Return a freshly signed IAM auth token without blocking the event loop."""
        # Signing itself is local, but resolving or refreshing AWS credentials (IRSA, instance
        # metadata, STS) can perform network I/O, so it must not run on the event loop.
        return await asyncio.to_thread(self._provider.get_token_password)

    def close(self) -> None:
        """Close the underlying boto3 client.

        Intentionally synchronous: unlike the Azure credential this performs no network I/O and
        cannot hang, so no timeout machinery is required.
        """
        self._provider.close()
