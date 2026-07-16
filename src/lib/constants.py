SERVICE_API = "api"
SERVICE_WORKER = "worker"
SERVICE_MIGRATIONS = "migrations"

EXECUTE_SCHEDULED_JOB_REF = "src.worker.jobs:execute_scheduled_job"
DEFAULT_PAGE_LIMIT = 20

HEARTBEAT_JOB_ID = "worker_heartbeat"
HEARTBEAT_JOB_NAME = "worker_heartbeat"
API_KEY_HEADER = "Api-Key"

MCP_SERVER_NAME = "aidial-scheduler"
MCP_BACKING_BASE_URL = "http://scheduler.internal"

# DIAL external-service on-behalf-of (OBO) credentials endpoint and the
# service-url template used to identify the external service in the request body.
OBO_CREDENTIALS_PATH = "/v1/ops/external-service/obo-credentials"
EXTERNAL_SERVICE_URL_TEMPLATE = "applications/{app_id}/external_services/{service_id}"
