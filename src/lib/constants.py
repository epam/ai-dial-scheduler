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

# DIAL storage/deployment endpoints used to create a conversation after a run completes.
BUCKET_PATH = "/v1/bucket"
APPLICATION_DETAILS_PATH_TEMPLATE = "/v1/applications/{application_id}"
CONVERSATION_PATH_TEMPLATE = "/v1/conversations/{conversation_path}"
# Folder segment under which scheduler-created conversations are stored, giving the
# convention conversations/{bucket}/.scheduler/{schedule_id}/{run_id}.
SCHEDULER_CONVERSATION_FOLDER = ".scheduler"
# Application deployment ids begin with this prefix; models use their deployment id as-is.
APPLICATION_DEPLOYMENT_PREFIX = "applications/"
