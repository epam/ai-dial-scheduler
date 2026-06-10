import os

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.lib.models.apscheduler_job import apscheduler_jobs
from src.lib.models.base import BaseSQLModel


_TEST_DB_URL = os.environ.get("TEST_DB_URL")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine():
    if _TEST_DB_URL is None:
        pytest.skip("TEST_DB_URL not set — skipping integration tests")

    eng = create_async_engine(_TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(BaseSQLModel.metadata.create_all)
        await conn.run_sync(apscheduler_jobs.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(BaseSQLModel.metadata.drop_all)
        await conn.run_sync(apscheduler_jobs.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def session(engine: sa.ext.asyncio.AsyncEngine) -> AsyncSession:
    async with AsyncSession(engine, expire_on_commit=False) as sess:
        yield sess
