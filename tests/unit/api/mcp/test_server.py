"""Unit tests for build_mcp_server tool inventory (T018)."""

from unittest.mock import MagicMock

import fastapi

from src.api.v1.endpoints.runs import router as runs_router
from src.api.v1.endpoints.schedules import router as schedules_router
from src.api.v1.endpoints.sign_in import router as sign_in_router


EXPECTED_TOOL_NAMES = {
    "list_schedules_v1_schedules",
    "create_schedule_v1_schedules",
    "get_schedule_v1_schedules",
    "update_schedule_v1_schedules",
    "pause_schedule_v1_schedules",
    "resume_schedule_v1_schedules",
    "delete_schedule_v1_schedules",
    "list_runs_v1_schedules",
    "get_run_v1_schedules",
    "get_sign_in_status",
}

EXCLUDED_NAME_FRAGMENTS = {"health", "echo"}


def _build_minimal_app(echo_enabled: bool = False) -> fastapi.FastAPI:
    app = fastapi.FastAPI()
    app.include_router(schedules_router, prefix="/v1")
    app.include_router(runs_router, prefix="/v1")
    app.include_router(sign_in_router, prefix="/v1")
    if echo_enabled:
        from src.api.v1.endpoints.echo import router as echo_router

        app.include_router(echo_router)
    return app


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


class TestBuildMcpServerInventory:
    async def test_produces_exactly_ten_tools(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_minimal_app()
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        assert len(tools) == 10

    async def test_tool_names_match_contract(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_minimal_app()
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        tool_names = {t.name for t in tools}
        assert tool_names == EXPECTED_TOOL_NAMES

    async def test_echo_routes_excluded_when_echo_enabled(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_minimal_app(echo_enabled=True)
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        tool_names = {t.name for t in tools}
        for fragment in EXCLUDED_NAME_FRAGMENTS:
            assert all(fragment not in name for name in tool_names), (
                f"Tool with excluded fragment '{fragment}' found in: {tool_names}"
            )

    async def test_health_check_excluded(self) -> None:
        from fastmcp import Client

        from src.api.mcp.server import build_mcp_server

        app = _build_minimal_app()
        mcp_server, _ = build_mcp_server(app, _mock_settings())

        async with Client(mcp_server) as client:
            tools = await client.list_tools()

        tool_names = {t.name for t in tools}
        assert all("health" not in name for name in tool_names)
