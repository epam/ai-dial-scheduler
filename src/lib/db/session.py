from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.db.engine import Database


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    database = Database()
    async with database.sessionmaker() as session:
        yield session
