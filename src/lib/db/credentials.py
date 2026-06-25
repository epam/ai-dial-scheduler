import asyncio

from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
from google.cloud.sql.connector import Connector, IPTypes


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
