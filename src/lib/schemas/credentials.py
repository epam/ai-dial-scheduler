from typing import Any, Literal

from pydantic import BaseModel, SecretStr


class OboCredentialsRequest(BaseModel):
    url: str
    owner_sub: str


class OboCredentials(BaseModel):
    header_name: str
    header_value: SecretStr


class RunError(BaseModel):
    stage: Literal["credentials", "target"]
    error_text: str
    credentials_response: dict[str, Any] | None = None
