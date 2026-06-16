from fastmcp.server.providers.openapi.routing import MCPType, RouteMap


ROUTE_MAPS: list[RouteMap] = [
    RouteMap(tags={"schedules"}, mcp_type=MCPType.TOOL),
    RouteMap(tags={"runs"}, mcp_type=MCPType.TOOL),
    RouteMap(mcp_type=MCPType.EXCLUDE),
]
