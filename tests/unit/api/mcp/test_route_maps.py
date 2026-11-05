"""Unit tests for MCP route maps — schedules/runs → TOOL, everything else → EXCLUDE (T017)."""

from fastmcp.server.providers.openapi.routing import MCPType, RouteMap, _determine_route_type


def _make_route(method: str, path: str, tags: list[str]) -> object:
    """Create a minimal HTTPRoute-compatible object for testing."""
    from fastmcp.utilities.openapi import HTTPRoute

    return HTTPRoute(
        method=method,
        path=path,
        operation_id=f"op_{method.lower()}_{path.replace('/', '_')}",
        tags=tags,
    )


class TestRouteMaps:
    def _get_maps(self) -> list[RouteMap]:
        from src.api.mcp.route_maps import ROUTE_MAPS

        return ROUTE_MAPS

    def test_schedules_tag_maps_to_tool(self) -> None:
        route = _make_route("GET", "/v1/schedules/", ["schedules"])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.TOOL

    def test_runs_tag_maps_to_tool(self) -> None:
        route = _make_route("GET", "/v1/schedules/{id}/runs", ["runs"])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.TOOL

    def test_post_schedules_maps_to_tool(self) -> None:
        route = _make_route("POST", "/v1/schedules/", ["schedules"])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.TOOL

    def test_no_tags_maps_to_exclude(self) -> None:
        route = _make_route("GET", "/health", [])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.EXCLUDE

    def test_echo_tag_maps_to_exclude(self) -> None:
        route = _make_route("POST", "/echo/rest", ["echo"])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.EXCLUDE

    def test_unknown_tag_maps_to_exclude(self) -> None:
        route = _make_route("GET", "/internal/metrics", ["internal"])
        result = _determine_route_type(route, self._get_maps())
        assert result.mcp_type == MCPType.EXCLUDE
