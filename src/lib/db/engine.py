import asyncio
import threading
from typing import Any, cast

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import Pool

from src.lib.db.credentials import AsyncAzureTokenProvider, create_gcp_connector, gcp_ip_type
from src.lib.db.enums import DbAuthMode
from src.lib.db.schema import _set_search_path_statement
from src.lib.db.url import build_database_url
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


_CREDENTIALS_CLOSE_TIMEOUT_SECONDS = 3


def _safe_close_gcp_connector(connector: Any) -> None:
    """Close a GCP Connector on a worker thread, swallowing errors.

    Errors are intentionally swallowed here — the caller already logs a warning if the close
    times out, and raising on the worker thread would only surface as an unhandled thread
    exception at interpreter shutdown.
    """
    try:
        connector.close()
    except Exception:
        pass


async def _close_azure_credential_with_timeout(provider: AsyncAzureTokenProvider, timeout: float) -> None:
    """Close an Azure async credential with a timeout, swallowing errors.

    The Azure credential's ``close()`` tears down the underlying transport, which can hang if
    the identity endpoint is unreachable. We bound the wait so the caller is never blocked
    indefinitely. Errors are swallowed for the same reason as the GCP helper above.
    """
    try:
        await asyncio.wait_for(provider.close(), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "Timeout on Azure credential close; proceeding without blocking.",
            timeout_seconds=timeout,
        )
    except Exception:
        pass


class Database:
    _instance: "Database | None" = None

    def __new__(cls) -> "Database":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return

        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._gcp_connector: Any | None = None
        self._azure_credential: AsyncAzureTokenProvider | None = None
        self._initialized = True

    def init_engine(self, *, poolclass: type[Pool] | None = None, force_fresh: bool = False) -> AsyncEngine:
        settings = get_settings()
        if self._engine is not None and not force_fresh:
            return self._engine

        if self._engine is not None and force_fresh:
            self._engine.sync_engine.dispose()
            self._engine = None
            self._sessionmaker = None
            self._release_credentials_best_effort()

        mode = settings.db.db_auth_mode
        logger.info(f"Initializing a new DB engine with {mode} auth mode.")

        if mode in (DbAuthMode.PASSWORD, DbAuthMode.CONNECTION_STRING):
            engine = self._init_engine_url_based(poolclass=poolclass)
        elif mode == DbAuthMode.GCP_IAM:
            engine = self._init_engine_gcp_iam(poolclass=poolclass)
        elif mode == DbAuthMode.AZURE_ENTRA:
            engine = self._init_engine_azure_entra(poolclass=poolclass)
        else:
            raise ValueError(f"Unsupported DB auth mode: {mode}")

        sessionmaker = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
        )
        self._engine = engine
        self._sessionmaker = sessionmaker
        return engine

    def _engine_kwargs(self, *, poolclass: type[Pool] | None) -> dict[str, Any]:
        settings = get_settings()
        engine_kwargs: dict[str, Any] = {
            "echo": settings.db.db_echo,
            "pool_pre_ping": settings.db.db_pool_pre_ping,
        }
        if poolclass is not None:
            engine_kwargs["poolclass"] = poolclass
        else:
            engine_kwargs["pool_size"] = settings.db.db_pool_size
            engine_kwargs["max_overflow"] = settings.db.db_max_overflow
            engine_kwargs["pool_recycle"] = settings.db.db_pool_recycle_seconds
            engine_kwargs["pool_timeout"] = settings.db.db_pool_timeout_seconds
        return engine_kwargs

    def _init_engine_url_based(self, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
        settings = get_settings()
        url = build_database_url(settings.db)
        kwargs = self._engine_kwargs(poolclass=poolclass)
        if settings.db.db_schema is not None:
            # asyncpg honours server_settings at connect time to set session-level GUCs.
            kwargs["connect_args"] = {"server_settings": {"search_path": settings.db.db_schema}}
        return create_async_engine(url, **kwargs)

    def _init_engine_gcp_iam(self, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
        settings = get_settings()
        ip_type = gcp_ip_type(private=settings.db.db_gcp_private_ip)

        # The connector must be created on the running event loop so that connect_async()
        # is always invoked on its originating loop.
        if self._gcp_connector is None:
            self._gcp_connector = create_gcp_connector(loop=asyncio.get_running_loop())
        connector = self._gcp_connector

        # Validated as non-None for gcp_iam mode by DatabaseSettings.validate_auth_requirements.
        instance_connection_name = cast(str, settings.db.db_gcp_instance_connection_name)
        schema = settings.db.db_schema

        async def getconn() -> Any:
            try:
                conn = await connector.connect_async(
                    instance_connection_name,
                    "asyncpg",
                    user=settings.db.db_gcp_iam_user,
                    db=settings.db.db_name,
                    ip_type=ip_type,
                    enable_iam_auth=True,
                )
                if schema is not None:
                    await conn.execute(_set_search_path_statement(schema))
                return conn
            except Exception as exc:
                # Log mode + exception type only — never the exception args, which could carry secrets.
                logger.error(
                    "DB credential acquisition failed.",
                    mode=DbAuthMode.GCP_IAM.value,
                    error_type=type(exc).__name__,
                )
                raise

        return create_async_engine(
            "postgresql+asyncpg://", async_creator=getconn, **self._engine_kwargs(poolclass=poolclass)
        )

    def _init_engine_azure_entra(self, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
        settings = get_settings()
        if self._azure_credential is None:
            self._azure_credential = AsyncAzureTokenProvider(client_id=settings.db.db_azure_managed_identity_client_id)
        provider = self._azure_credential

        # A fresh token is fetched for every NEW connection. Tokens are only checked by PostgreSQL
        # at connect time, so already-pooled connections keep working past token expiry — token TTL
        # is therefore independent of pool_recycle and no special recycling is required here.
        schema = settings.db.db_schema

        async def getconn() -> Any:
            try:
                token = await provider.get_token_password()
                conn = await asyncpg.connect(
                    host=settings.db.db_host,
                    port=settings.db.db_port,
                    user=settings.db.db_azure_entra_user,
                    password=token,
                    database=settings.db.db_name,
                    ssl="require",
                )
                if schema is not None:
                    await conn.execute(_set_search_path_statement(schema))
                return conn
            except Exception as exc:
                # Log mode + exception type only — never the exception args, which could carry secrets.
                logger.error(
                    "DB credential acquisition failed.",
                    mode=DbAuthMode.AZURE_ENTRA.value,
                    error_type=type(exc).__name__,
                )
                raise

        return create_async_engine(
            "postgresql+asyncpg://", async_creator=getconn, **self._engine_kwargs(poolclass=poolclass)
        )

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self.init_engine()
        return self._engine  # type: ignore[return-value]

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self.init_engine()
        return self._sessionmaker  # type: ignore[return-value]

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
        await self._dispose_gcp_connector()
        await self._dispose_azure_credential()

    async def _dispose_gcp_connector(self) -> None:
        if self._gcp_connector is not None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._gcp_connector.close),
                    timeout=_CREDENTIALS_CLOSE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning("Timeout on GCP Connector dispose. Skip closing.")
            finally:
                self._gcp_connector = None

    async def _dispose_azure_credential(self) -> None:
        if self._azure_credential is not None:
            try:
                await self._azure_credential.close()
            finally:
                self._azure_credential = None

    def _release_credentials_best_effort(self) -> None:
        """Release workload-identity resources on a synchronous re-init (force_fresh).

        Unlike ``dispose`` this runs in a sync context, so the GCP connector (sync ``close``) is
        closed on a daemon thread with a timeout and the Azure async credential is closed via
        ``_close_azure_credential_with_timeout`` — either scheduled on the running loop or run
        to completion (with its own timeout) via ``asyncio.run``. This prevents leaking the
        connector's refresh tasks and the credential's transport when the engine is rebuilt,
        without hanging the caller if either ``close()`` blocks.
        """
        if self._gcp_connector is not None:
            connector = self._gcp_connector
            self._gcp_connector = None
            thread = threading.Thread(target=_safe_close_gcp_connector, args=(connector,), daemon=True)
            thread.start()
            thread.join(timeout=_CREDENTIALS_CLOSE_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning(
                    "Timeout on sync GCP connector close; proceeding without blocking.",
                    timeout_seconds=_CREDENTIALS_CLOSE_TIMEOUT_SECONDS,
                )

        if self._azure_credential is not None:
            provider = self._azure_credential
            self._azure_credential = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                # Keep a strong reference so the task is not GC'd before it completes.
                task = loop.create_task(
                    _close_azure_credential_with_timeout(provider, _CREDENTIALS_CLOSE_TIMEOUT_SECONDS)
                )
                task.add_done_callback(lambda _t: None)
            else:
                asyncio.run(_close_azure_credential_with_timeout(provider, _CREDENTIALS_CLOSE_TIMEOUT_SECONDS))
