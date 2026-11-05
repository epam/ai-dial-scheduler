from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ExternalServiceAuthStatus(BaseModel):
    """The caller's own auth status for one external service, from ``GET /v1/applications/{id}``.

    Both fields reflect the identity behind the ``Api-Key`` used for that request — they answer
    "is the caller signed in", not an arbitrary other user's status.
    """

    model_config = ConfigDict(extra="ignore")

    user_level_auth_status: Literal["SIGNED_IN", "SIGNED_OUT", "FAILED"]
    app_level_auth_status: Literal["SIGNED_IN", "SIGNED_OUT", "FAILED"]


class SignInStatus(BaseModel):
    """Outcome of checking one user's connection to one external service.

    ``state`` is the machine-readable field a caller MUST branch on; ``signed_in`` is a
    convenience boolean derived from it and MUST NOT be used to distinguish
    ``not_signed_in`` from ``unknown``. ``detail`` is free text safe to relay verbatim to a
    user, but is not a stable contract and MUST NOT be parsed by any consumer.
    """

    service_id: str = Field(min_length=1, pattern=r"\S")
    state: Literal["signed_in", "not_signed_in", "unknown"]
    detail: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def signed_in(self) -> bool:
        return self.state == "signed_in"
