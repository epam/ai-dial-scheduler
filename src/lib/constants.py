SERVICE_API = "api"
SERVICE_WORKER = "worker"
SERVICE_MIGRATIONS = "migrations"

EXECUTE_SCHEDULED_JOB_REF = "src.worker.jobs:execute_scheduled_job"
DEFAULT_PAGE_LIMIT = 20
MAX_CRON_ENTRIES = 20
DEFAULT_SCHEDULE_TIMEZONE = "UTC"
# DIAL Core paths appended to settings.dial_url when a chat_completion/responses schedule omits ``url``.
# The OpenAI SDK appends the endpoint itself: "/deployments/{model}/chat/completions" or "/responses".
DEFAULT_CHAT_COMPLETION_URL_PATH = "/openai/"
DEFAULT_RESPONSES_URL_PATH = "/openai/v1"

HEARTBEAT_JOB_ID = "worker_heartbeat"
HEARTBEAT_JOB_NAME = "worker_heartbeat"
API_KEY_HEADER = "Api-Key"

# Sent on every outbound request so DIAL Core (logs, traces) can tell scheduler traffic apart.
USER_AGENT_HEADER = "User-Agent"
USER_AGENT_PRODUCT = "ai-dial-scheduler"
# DIAL Core and DIAL Admin key sessions on this header; chat completion runs send their conversation id in it.
CONVERSATION_ID_HEADER = "X-CONVERSATION-ID"
# Upper bound for the encoded header value; proxies and DIAL Core reject header sets above ~8 KiB, which would
# turn a successful run into a failed one. Longer ids are not sent as a header (the conversation is still saved).
MAX_CONVERSATION_ID_HEADER_BYTES = 4096
# Bound on resolving a run's conversation location (bucket lookup) before the target request. It runs outside the
# run's own timeout and retry policy, so a slow bucket endpoint must not delay every run by a full HTTP timeout.
CONVERSATION_LOCATION_TIMEOUT_SECONDS = 10.0
# Auth headers the OpenAI SDK emits for AsyncAzureOpenAI (``api-key``; ``Authorization`` for AD tokens). A caller
# copy in ``extra_headers`` would be sent alongside the OBO value and could override the run's identity.
SDK_AUTH_HEADERS = ("api-key", "Authorization")

MCP_SERVER_NAME = "aidial-scheduler"
MCP_BACKING_BASE_URL = "http://scheduler.internal"

# DIAL external-service on-behalf-of (OBO) credentials endpoint and the
# service-url template used to identify the external service in the request body.
OBO_CREDENTIALS_PATH = "/v1/ops/external-service/obo-credentials"
EXTERNAL_SERVICE_URL_TEMPLATE = "applications/{app_id}/external_services/{service_id}"

# Application details endpoint, whose response carries per-service
# user_level_auth_status/app_level_auth_status for the calling identity.
APPLICATION_STATUS_PATH_TEMPLATE = "/v1/applications/{app_id}"

# Client-channel interactive sign-in: lets a not-signed-in check push a popup into the
# chat session that made the MCP call, via the channel id it forwarded on the request.
CLIENT_CHANNEL_HEADER = "X-DIAL-CLIENT-CHANNEL-ID"
CLIENT_CHANNEL_INTERACT_PATH = "/v1/ops/client-channel/interact"
EXTERNAL_SERVICE_SIGNIN_METHOD = "external-service/signin"

# DIAL storage/deployment endpoints used to create a conversation after a run completes.
BUCKET_PATH = "/v1/bucket"
APPLICATION_DETAILS_PATH_TEMPLATE = "/v1/{application_id}"
CONVERSATION_PATH_TEMPLATE = "/v1/conversations/{conversation_path}"
# Folder segment under which scheduler-created conversations are stored, giving the
# convention conversations/{bucket}/.scheduler/{schedule_id}/{run_id}.
SCHEDULER_CONVERSATION_FOLDER = ".scheduler"
# Application deployment ids begin with this prefix; models use their deployment id as-is.
APPLICATION_DEPLOYMENT_PREFIX = "applications/"

# Run conversation path leaf convention: {deployment-name}__{display-name}__{run-id}, where the
# parts are stored raw (only path separators are escaped). The deployment id's folder segments are
# nested in the path instead. The document's own name is the schedule display name alone.
CONVERSATION_NAME_SEPARATOR = "__"
# Prefix distinguishing a conversation document id from its storage resource path.
CONVERSATION_ID_PREFIX = "conversations/"
