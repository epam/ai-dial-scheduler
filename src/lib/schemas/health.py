from typing import Literal

from pydantic import BaseModel


class ReadinessResponse(BaseModel):
    """Response schema for the GET /ready readiness endpoint."""

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unavailable"]
