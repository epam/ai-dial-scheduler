"""Integration tests for the generated ``model`` column and the model filter.

These cover what mocks cannot: that PostgreSQL derives ``model`` from ``kwargs`` on insert and
update, that it is not writable, and that filtering matches case-insensitively while skipping
schedules whose payload has no model.

Requires TEST_DB_URL to be set; all tests are skipped otherwise.
"""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock
import uuid

from fastapi import FastAPI
import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.api.dependencies.auth import get_user_id
from src.api.dependencies.db import get_db_session
from src.api.dependencies.scheduler import get_scheduler
from src.api.dependencies.sign_in import get_sign_in_service
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.models.schedule import Schedule
from src.lib.models.schedule_run import ScheduleRun
from src.lib.repositories.schedule_repository import ScheduleRepository
from tests.integration.conftest import make_signed_in_service


pytestmark = pytest.mark.asyncio(loop_scope="module")


OWNER = "mock_user_id"


@pytest.fixture
def scheduler() -> MagicMock:
    return MagicMock()


@pytest.fixture
def test_app(engine: AsyncEngine, scheduler: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(schedules_router, prefix="/v1")

    async def override_db() -> Any:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    async def override_user_id() -> str:
        return OWNER

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_user_id] = override_user_id
    app.dependency_overrides[get_sign_in_service] = make_signed_in_service
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    return app


@pytest_asyncio.fixture(loop_scope="module")
async def client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app),
        base_url="http://test",
        headers={"Api-Key": "test-key"},
    ) as c:
        yield c


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def cleanup(engine: AsyncEngine) -> AsyncGenerator[None, None]:
    yield
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(sa.delete(ScheduleRun))
        await session.execute(sa.delete(Schedule))
        await session.commit()


def chat_kwargs(model: Any) -> dict[str, Any]:
    return {
        "target_type": "chat_completion",
        "url": "https://core.dial/openai/",
        "payload": {"model": model, "messages": [{"role": "user", "content": "hi"}]},
        "api_version": "2025-01-01-preview",
    }


REST_KWARGS: dict[str, Any] = {
    "target_type": "rest",
    "method": "POST",
    "url": "https://example.com/hook",
    "payload": {"event": "tick"},
}


async def insert(engine: AsyncEngine, kwargs: dict[str, Any], *, display_name: str = "S") -> str:
    sched_id = f"model-{uuid.uuid4()}"
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Schedule(
                id=sched_id,
                display_name=display_name,
                created_by=OWNER,
                service_id="dial-native",
                trigger={"cron": {"fields": {"hour": 9}}},
                kwargs=kwargs,
            )
        )
        await session.commit()
    return sched_id


async def stored_model(engine: AsyncEngine, sched_id: str) -> str | None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await session.scalar(sa.select(Schedule.model).where(Schedule.id == sched_id))


class TestGeneratedColumn:
    async def test_derived_from_payload_model(self, engine: AsyncEngine) -> None:
        sched_id = await insert(engine, chat_kwargs("gpt-4o"))

        assert await stored_model(engine, sched_id) == "gpt-4o"

    async def test_null_when_payload_has_no_model(self, engine: AsyncEngine) -> None:
        """REST and SSE targets simply have no model; the column must be NULL, not empty."""
        sched_id = await insert(engine, REST_KWARGS)

        assert await stored_model(engine, sched_id) is None

    async def test_null_when_payload_is_absent(self, engine: AsyncEngine) -> None:
        sched_id = await insert(engine, {"target_type": "rest", "method": "GET", "url": "https://e.com"})

        assert await stored_model(engine, sched_id) is None

    async def test_non_string_model_is_rendered_as_text(self, engine: AsyncEngine) -> None:
        """``->>`` stringifies any scalar; the column must never break on odd payloads."""
        sched_id = await insert(engine, chat_kwargs(42))

        assert await stored_model(engine, sched_id) == "42"

    async def test_null_when_model_is_json_null(self, engine: AsyncEngine) -> None:
        sched_id = await insert(engine, chat_kwargs(None))

        assert await stored_model(engine, sched_id) is None

    async def test_follows_an_update_to_kwargs(self, engine: AsyncEngine) -> None:
        """The column is generated, so it cannot drift from kwargs after an edit."""
        sched_id = await insert(engine, chat_kwargs("gpt-4o"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            schedule = await session.get(Schedule, sched_id)
            assert schedule is not None
            schedule.kwargs = chat_kwargs("claude-sonnet-5")
            await session.commit()

        assert await stored_model(engine, sched_id) == "claude-sonnet-5"

    async def test_becomes_null_when_a_model_is_removed(self, engine: AsyncEngine) -> None:
        sched_id = await insert(engine, chat_kwargs("gpt-4o"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            schedule = await session.get(Schedule, sched_id)
            assert schedule is not None
            schedule.kwargs = REST_KWARGS
            await session.commit()

        assert await stored_model(engine, sched_id) is None

    async def test_supplying_a_value_is_rejected_by_the_database(self, engine: AsyncEngine) -> None:
        """The column is database-owned: a write attempt fails loudly rather than taking effect."""
        async with AsyncSession(engine, expire_on_commit=False) as session:
            session.add(
                Schedule(
                    id=f"model-{uuid.uuid4()}",
                    display_name="S",
                    created_by=OWNER,
                    service_id="dial-native",
                    trigger={"cron": {"fields": {"hour": 9}}},
                    kwargs=chat_kwargs("gpt-4o"),
                    model="attacker-supplied",
                )
            )
            with pytest.raises(ProgrammingError, match="generated column"):
                await session.commit()
            await session.rollback()


class TestRepositoryModelFilter:
    async def test_matches_case_insensitive_substring(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, chat_kwargs("gpt-4.1-mini-2025-04-14"))
        await insert(engine, chat_kwargs("claude-sonnet-5"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(OWNER, 20, 0, model_filter="GPT-4")

        assert total == 2
        assert {row[0].model for row in rows} == {"gpt-4o", "gpt-4.1-mini-2025-04-14"}

    async def test_excludes_schedules_without_a_model(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, REST_KWARGS)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(OWNER, 20, 0, model_filter="gpt")

        assert total == 1
        assert rows[0][0].model == "gpt-4o"

    async def test_no_filter_returns_rows_with_and_without_a_model(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, REST_KWARGS)

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(OWNER, 20, 0)

        assert total == 2
        assert {row[0].model for row in rows} == {"gpt-4o", None}

    async def test_wildcards_in_the_term_match_literally(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, chat_kwargs("gpt%4o"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(OWNER, 20, 0, model_filter="gpt%4")

        assert total == 1
        assert rows[0][0].model == "gpt%4o"

    async def test_underscore_in_the_term_matches_literally(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt_4o"))
        await insert(engine, chat_kwargs("gptX4o"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, _ = await ScheduleRepository(session).list(OWNER, 20, 0, model_filter="gpt_4")

        assert total == 1

    async def test_composes_with_the_name_filter(self, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"), display_name="Nightly report")
        await insert(engine, chat_kwargs("gpt-4o"), display_name="Weekly digest")

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(
                OWNER, 20, 0, model_filter="gpt", name_filter="nightly"
            )

        assert total == 1
        assert rows[0][0].display_name == "Nightly report"

    async def test_count_reflects_the_filter_across_pages(self, engine: AsyncEngine) -> None:
        for _ in range(3):
            await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, chat_kwargs("claude-sonnet-5"))

        async with AsyncSession(engine, expire_on_commit=False) as session:
            total, rows = await ScheduleRepository(session).list(OWNER, 2, 0, model_filter="gpt")

        assert total == 3
        assert len(rows) == 2


class TestListEndpoint:
    async def test_model_is_exposed_on_list_rows(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))

        body = (await client.get("/v1/schedules/")).json()

        assert body["results"][0]["model"] == "gpt-4o"

    async def test_model_is_null_for_targets_without_one(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        await insert(engine, REST_KWARGS)

        body = (await client.get("/v1/schedules/")).json()

        assert body["results"][0]["model"] is None

    async def test_filters_by_model(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, chat_kwargs("claude-sonnet-5"))
        await insert(engine, REST_KWARGS)

        body = (await client.get("/v1/schedules/?model=gpt")).json()

        assert body["count"] == 1
        assert body["results"][0]["model"] == "gpt-4o"

    async def test_blank_model_parameter_does_not_filter(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))
        await insert(engine, REST_KWARGS)

        body = (await client.get("/v1/schedules/?model=%20%20")).json()

        assert body["count"] == 2

    async def test_page_links_preserve_the_model_filter(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        """Without this the second page would silently drop the filter."""
        for _ in range(3):
            await insert(engine, chat_kwargs("gpt-4o"))

        body = (await client.get("/v1/schedules/?model=gpt-4&limit=2")).json()

        assert body["count"] == 3
        assert "model=gpt-4" in body["next"]

    async def test_unmatched_model_returns_an_empty_page(self, client: httpx.AsyncClient, engine: AsyncEngine) -> None:
        await insert(engine, chat_kwargs("gpt-4o"))

        body = (await client.get("/v1/schedules/?model=llama")).json()

        assert body["count"] == 0
        assert body["results"] == []
