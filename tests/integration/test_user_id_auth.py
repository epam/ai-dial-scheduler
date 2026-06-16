"""Integration tests for user-identity authentication and per-identity data isolation.

Exercises the full HTTP → service → repository → database stack with mocked DIAL responses.
All endpoints use the real get_user_id dependency backed by a mocked DialClient.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.

US1: Per-identity isolation — two identities see disjoint schedules.
US2: Auth rejections — missing Api-Key, DIAL-rejected key → 401; no data created.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.api.dependencies.auth import get_dial_client, get_settings_dep
from src.api.dependencies.db import get_db_session
from src.api.dependencies.scheduler import get_scheduler
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.exceptions.errors import DialServiceError
from src.lib.models.schedule import Schedule
from src.lib.settings import Settings


pytestmark = pytest.mark.asyncio(loop_scope="module")


ALICE_ID = "alice@example.com"
BOB_ID = "bob@example.com"

ALICE_DIAL_RESPONSE = {"userClaims": {"email": [ALICE_ID]}}
BOB_DIAL_RESPONSE = {"userClaims": {"email": [BOB_ID]}}

CRON_REQUEST: dict[str, Any] = {
    "display_name": "Auth Test Schedule",
    "trigger": {"cron": {"fields": {"hour": 9, "minute": 0}}},
    "properties": {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/deployments/gpt-4o/chat/completions",
        "api_version": "2025-01-01-preview",
    },
}


def make_mock_settings() -> MagicMock:
    mock = MagicMock(spec=Settings)
    mock.user_id_path_list = ["userClaims.email.0", "project"]
    return mock


@pytest.fixture(scope="module")
def test_app(engine: AsyncEngine) -> FastAPI:
    app = FastAPI()
    app.include_router(schedules_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings_dep] = make_mock_settings
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


def _make_dial_client(response_data: dict) -> AsyncMock:
    mock = AsyncMock()
    mock.get_user_info.return_value = response_data
    return mock


def _make_rejected_dial_client() -> AsyncMock:
    mock = AsyncMock()
    mock.get_user_info.side_effect = DialServiceError("rejected")
    return mock


# ---------------------------------------------------------------------------
# US1: Per-identity isolation
# ---------------------------------------------------------------------------


class TestPerIdentityIsolation:
    async def test_alice_schedule_not_visible_to_bob(
        self, test_app: FastAPI, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(ALICE_DIAL_RESPONSE)
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST, headers={"Api-Key": "alice-key"})
        assert create_resp.status_code == 201
        assert create_resp.json()["created_by"] == ALICE_ID

        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(BOB_DIAL_RESPONSE)
        list_resp = await client.get("/v1/schedules/", headers={"Api-Key": "bob-key"})
        assert list_resp.status_code == 200
        bob_ids = {item["id"] for item in list_resp.json()["results"]}
        assert create_resp.json()["id"] not in bob_ids

    async def test_alice_can_retrieve_her_own_schedule(self, test_app: FastAPI, client: httpx.AsyncClient) -> None:
        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(ALICE_DIAL_RESPONSE)
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST, headers={"Api-Key": "alice-key"})
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]

        get_resp = await client.get(f"/v1/schedules/{schedule_id}", headers={"Api-Key": "alice-key"})
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == schedule_id

    async def test_bob_cannot_access_alices_schedule_by_id(self, test_app: FastAPI, client: httpx.AsyncClient) -> None:
        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(ALICE_DIAL_RESPONSE)
        create_resp = await client.post("/v1/schedules/", json=CRON_REQUEST, headers={"Api-Key": "alice-key"})
        assert create_resp.status_code == 201
        schedule_id = create_resp.json()["id"]

        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(BOB_DIAL_RESPONSE)
        get_resp = await client.get(f"/v1/schedules/{schedule_id}", headers={"Api-Key": "bob-key"})
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# US2: Auth rejections — no data created
# ---------------------------------------------------------------------------


class TestAuthRejections:
    async def test_missing_api_key_returns_422(
        self, test_app: FastAPI, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        test_app.dependency_overrides[get_dial_client] = lambda: _make_dial_client(ALICE_DIAL_RESPONSE)
        initial_count = await _count_schedules(engine)

        response = await client.post("/v1/schedules/", json=CRON_REQUEST)

        assert response.status_code == 422
        assert await _count_schedules(engine) == initial_count

    async def test_dial_error_returns_502_no_data_created(
        self, test_app: FastAPI, client: httpx.AsyncClient, engine: AsyncEngine
    ) -> None:
        test_app.dependency_overrides[get_dial_client] = lambda: _make_rejected_dial_client()
        initial_count = await _count_schedules(engine)

        response = await client.post("/v1/schedules/", json=CRON_REQUEST, headers={"Api-Key": "bad-key"})

        assert response.status_code == 502
        assert await _count_schedules(engine) == initial_count


async def _count_schedules(engine: AsyncEngine) -> int:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(sa.select(sa.func.count()).select_from(Schedule))
        return result.scalar_one()
