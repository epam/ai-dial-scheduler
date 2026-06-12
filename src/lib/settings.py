from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.lib.db.enums import DbAuthMode, DbDriver


ENV_FILE_MODEL_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class DatabaseSettings(BaseSettings):
    model_config = ENV_FILE_MODEL_CONFIG

    db_driver: DbDriver = DbDriver.ASYNC
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "app"
    db_user: str = "app"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800
    db_pool_pre_ping: bool = True
    db_echo: bool = False
    db_auth_mode: DbAuthMode = DbAuthMode.PASSWORD

    db_password: str | None = None
    db_url: str | None = None

    @model_validator(mode="after")
    def validate_auth_requirements(self) -> "DatabaseSettings":
        required_fields_by_mode: dict[DbAuthMode, tuple[str, ...]] = {
            DbAuthMode.PASSWORD: ("db_password",),
            DbAuthMode.CONNECTION_STRING: ("db_url",),
        }
        required_fields = required_fields_by_mode.get(self.db_auth_mode, ())
        missing = [field for field in required_fields if getattr(self, field) is None]
        if missing:
            fields_label = ", ".join(missing)
            raise ValueError(f"DB auth mode '{self.db_auth_mode.value}' requires: {fields_label}.")
        return self


class Settings(BaseSettings):
    model_config = ENV_FILE_MODEL_CONFIG

    env: str = "prod"
    app_name: str = "aidial-scheduler"
    dial_url: str
    dial_api_key: SecretStr
    port: int = 5000
    log_level: str = "INFO"
    noop_interval_seconds: int = 60
    http_timeout_seconds: float = 30.0
    echo_endpoints: bool = False
    job_coalesce: bool = False
    job_max_instances: int = 1
    job_misfire_grace_time: int = 60
    user_id_path_list: list[str] = ["userClaims.email.0", "project"]

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
