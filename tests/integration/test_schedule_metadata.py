"""Integration tests for schedule metadata and metadata filtering.

Exercises the full HTTP -> service -> repository -> database stack for the
metadata column and the ``metadata__<key>`` list filter. APScheduler is mocked.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.db import get_db_session
from src.api.dependencies.scheduler import get_scheduler
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.models.schedule import Schedule


pytestmark = pytest.mark.asyncio(loop_scope="module")


INTEGRATION_USER_ID = "mock_user_id"

BASE_REQUEST: dict[str, Any] = {
    "display_name": "Metadata Test Schedule",
    "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
        "api_version": "2025-01-01-preview",
    },
}


@pytest.fixture(scope="module")
def test_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.include_router(schedules_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def override_user_id() -> str:
        return INTEGRATION_USER_ID

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_user_id] = override_user_id
    return app


@pytest.fixture
def mock_scheduler() -> MagicMock:
    return MagicMock()


@pytest_asyncio.fixture(loop_scope="module")
async def client(test_app: FastAPI, mock_scheduler: MagicMock) -> AsyncGenerator[httpx.AsyncClient, None]:
    test_app.dependency_overrides[get_scheduler] = lambda: mock_scheduler
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def cleanup(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    yield
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(Schedule))
        await session.commit()


class TestMetadataRoundTrip:
    async def test_create_with_metadata_then_get_returns_it(self, client: httpx.AsyncClient) -> None:
        request = {**BASE_REQUEST, "metadata": {"tenant": "hcscd", "env": "prod"}}
        create_resp = await client.post("/v1/schedules/", json=request)
        assert create_resp.status_code == 201
        assert create_resp.json()["metadata"] == {"tenant": "hcscd", "env": "prod"}

        schedule_id = create_resp.json()["id"]
        get_resp = await client.get(f"/v1/schedules/{schedule_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["metadata"] == {"tenant": "hcscd", "env": "prod"}

    async def test_create_without_metadata_defaults_to_empty(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json=BASE_REQUEST)
        assert create_resp.status_code == 201
        assert create_resp.json()["metadata"] == {}

    async def test_update_replaces_metadata(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd"}})
        schedule_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/v1/schedules/{schedule_id}", json={**BASE_REQUEST, "metadata": {"team": "platform"}}
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["metadata"] == {"team": "platform"}


class TestMetadataFiltering:
    async def test_single_key_filter(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd"}})
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "other"}})

        resp = await client.get("/v1/schedules/?metadata__tenant=hcscd")
        body = resp.json()
        assert body["count"] == 1

    async def test_multi_key_and_filter(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd", "env": "prod"}})
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd", "env": "dev"}})

        resp = await client.get("/v1/schedules/?metadata__tenant=hcscd&metadata__env=prod")
        assert resp.json()["count"] == 1

    async def test_string_only_matching_excludes_numeric(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"count": 5}})

        resp = await client.get("/v1/schedules/?metadata__count=5")
        assert resp.json()["count"] == 0

    async def test_unmatched_key_returns_empty(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd"}})

        resp = await client.get("/v1/schedules/?metadata__nonexistent=whatever")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_no_filter_returns_all_owned(self, client: httpx.AsyncClient) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd"}})
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "other"}})

        resp = await client.get("/v1/schedules/")
        assert resp.json()["count"] == 2

    async def test_filter_is_owner_scoped(self, client: httpx.AsyncClient, test_app: FastAPI) -> None:
        await client.post("/v1/schedules/", json={**BASE_REQUEST, "metadata": {"tenant": "hcscd"}})

        async def override_other_user() -> str:
            return "other_user_id"

        test_app.dependency_overrides[get_user_id] = override_other_user
        try:
            resp = await client.get("/v1/schedules/?metadata__tenant=hcscd")
        finally:
            test_app.dependency_overrides[get_user_id] = lambda: INTEGRATION_USER_ID

        assert resp.json()["count"] == 0
