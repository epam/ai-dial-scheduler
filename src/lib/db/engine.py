from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import Pool

from src.lib.db.url import build_database_url
from src.lib.settings import get_settings
from src.lib.utils.logging import logger


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
        self._initialized = True

    def init_engine(self, *, poolclass: type[Pool] | None = None, force_fresh: bool = False) -> AsyncEngine:
        settings = get_settings()
        if self._engine is not None and not force_fresh:
            return self._engine

        if self._engine is not None and force_fresh:
            self._engine = None
            self._sessionmaker = None

        logger.info(f"Initializing a new DB engine with {settings.db.db_auth_mode} auth mode.")
        engine = self._init_engine_url_based(poolclass=poolclass)

        self._sessionmaker = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
        )
        return engine

    def _init_engine_url_based(self, *, poolclass: type[Pool] | None = None) -> AsyncEngine:
        settings = get_settings()
        url = build_database_url(settings.db)

        engine_kwargs: dict[str, object] = {
            "echo": settings.db.db_echo,
            "pool_pre_ping": settings.db.db_pool_pre_ping,
        }

        if poolclass is not None:
            engine_kwargs["poolclass"] = poolclass
        else:
            engine_kwargs["pool_size"] = settings.db.db_pool_size
            engine_kwargs["max_overflow"] = settings.db.db_max_overflow
            engine_kwargs["pool_recycle"] = settings.db.db_pool_recycle_seconds

        engine = create_async_engine(url, **engine_kwargs)

        self._engine = engine
        return engine

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
