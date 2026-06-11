from enum import StrEnum


class DbAuthMode(StrEnum):
    PASSWORD = "password"
    CONNECTION_STRING = "connection-string"


class DbDriver(StrEnum):
    ASYNC = "postgresql+asyncpg"
    SYNC = "postgresql+psycopg2"


class ScheduleRunStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    IN_PROGRESS = "in_progress"
    MISSED = "missed"


class TargetType(StrEnum):
    CHAT_COMPLETION = "chat_completion"
    REST = "rest"
    SSE = "sse"


class HttpMethod(StrEnum):
    POST = "POST"
    PUT = "PUT"
    GET = "GET"


class TriggerType(StrEnum):
    CRON = "cron"
    DATE = "date"
