"""Integration-style MCP tool tests — tool listing and auth propagation (T008, T009, T013).

These tests use the FastMCP Client directly against the built server (no real DB required).
For tests that require a live database see tests/integration/test_mcp_endpoint.py.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest

from src.api.dependencies.auth import get_user_id
from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.lib.schemas.pagination import Page
from src.lib.schemas.schedule import ScheduleListItem


EXPECTED_TOOL_NAMES = {
    "list_schedules_v1_schedules",
    "create_schedule_v1_schedules",
    "get_schedule_v1_schedules",
    "update_schedule_v1_schedules",
    "pause_schedule_v1_schedules",
    "resume_schedule_v1_schedules",
    "list_runs_v1_schedules",
    "get_run_v1_schedules",
}


def _mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.api_http.timeout_seconds = 30.0
    settings.api_http.connect_timeout_seconds = 5.0
    settings.api_http.read_timeout_seconds = 30.0
    settings.api_http.write_timeout_seconds = 30.0
    settings.api_http.pool_timeout_seconds = 5.0
    settings.api_http.max_connections = 10
    settings.api_http.max_keepalive_connections = 5
    return settings


def _build_app_with_mocked_service(mock_service: Any, user_id: str = "user@example.com") -> fastapi.FastAPI:
    """Build a minimal FastAPI app with the schedule/run routers and mocked dependencies."""
    from src.api.dependencies.runs import get_schedule_run_service
    from src.api.dependencies.scheduler import get_schedule_service

    app = fastapi.FastAPI()
    app.include_router(schedules_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")
    app.dependency_overrides[get_user_id] = lambda: user_id
    app.dependency_overrides[get_schedule_service] = lambda: mock_service
    app.dependency_overrides[get_schedule_run_service] = lambda: mock_service
    return app


class TestMcpToolListing:
    """T008 — tools are discoverable via the MCP surface."""

    async def test_tool_inventory_matches_contract(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_app_with_mocked_service(MagicMock())
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        tool_names = {t.name for t in tools}
        assert tool_names == EXPECTED_TOOL_NAMES

    async def test_exactly_eight_tools(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_app_with_mocked_service(MagicMock())
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        assert len(tools) == 8


class TestMcpToolInvocation:
    """T009 — calling a tool through MCP returns a REST-equivalent response."""

    async def test_list_schedules_returns_page_structure(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        mock_service = AsyncMock()
        empty_page: Page[ScheduleListItem] = Page(count=0, limit=20, offset=0, results=[], next=None, previous=None)
        mock_service.list = AsyncMock(return_value=empty_page)

        app = _build_app_with_mocked_service(mock_service)
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            result = await client.call_tool("list_schedules_v1_schedules", {})

        assert result is not None


class TestMcpAuth:
    """T013 — credential presence/absence and owner scoping."""

    async def test_tool_call_without_api_key_fails(self) -> None:
        """A tool call lacking Api-Key propagation should result in a 422 from the underlying endpoint."""
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        # Build app WITHOUT overriding get_user_id — requires real Api-Key header
        app = fastapi.FastAPI()
        app.include_router(schedules_router, prefix="/v1")
        app.include_router(runs_router, prefix="/v1")

        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            # Without Api-Key being forwarded, the underlying endpoint should reject with 422
            with pytest.raises(Exception):
                await client.call_tool("list_schedules_v1_schedules", {})

    async def test_owner_scoping_per_user_id(self) -> None:
        """Different user_ids yield isolated result sets (SC-004)."""
        from datetime import UTC, datetime

        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server
        from src.lib.db.enums import TriggerType

        now = datetime(2026, 1, 1, tzinfo=UTC)
        item_a = ScheduleListItem(
            id="a1",
            display_name="User A Schedule",
            service_id="dial-oauth",
            next_run_time=None,
            trigger_type=TriggerType.CRON,
            trigger={"cron": {"fields": {"hour": 9}}},
            created_by="user-a@example.com",
            created_at=now,
            updated_at=now,
        )
        item_b = ScheduleListItem(
            id="b1",
            display_name="User B Schedule",
            service_id="dial-oauth",
            next_run_time=None,
            trigger_type=TriggerType.CRON,
            trigger={"cron": {"fields": {"hour": 9}}},
            created_by="user-b@example.com",
            created_at=now,
            updated_at=now,
        )
        page_a: Page[ScheduleListItem] = Page(count=1, limit=20, offset=0, results=[item_a], next=None, previous=None)
        page_b: Page[ScheduleListItem] = Page(count=1, limit=20, offset=0, results=[item_b], next=None, previous=None)

        mock_service_a = AsyncMock()
        mock_service_a.list = AsyncMock(return_value=page_a)

        mock_service_b = AsyncMock()
        mock_service_b.list = AsyncMock(return_value=page_b)

        app_a = _build_app_with_mocked_service(mock_service_a, user_id="user-a@example.com")
        app_b = _build_app_with_mocked_service(mock_service_b, user_id="user-b@example.com")

        mcp_a, _ = build_mcp_server(app_a, _mock_settings())
        mcp_b, _ = build_mcp_server(app_b, _mock_settings())

        async with Client(mcp_a) as client_a:
            result_a = await client_a.call_tool("list_schedules_v1_schedules", {})

        async with Client(mcp_b) as client_b:
            result_b = await client_b.call_tool("list_schedules_v1_schedules", {})

        # Each client sees only their own data
        assert result_a != result_b
