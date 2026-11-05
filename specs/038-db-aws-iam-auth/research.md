# Research: AWS RDS IAM Database Authentication

## Decision 1 — token generation library

**Decision**: `boto3.client("rds").generate_db_auth_token(DBHostname, Port, DBUsername, Region)`.

**Rationale**: This is the AWS-supported way to build an RDS IAM auth token and the direct analogue
of `azure-identity`'s `get_token` used by `azure_entra`. It is a **local** SigV4 presign of the
string `host:port/?Action=connect&DBUser=<user>` — no API call to AWS is made to mint the token.
Credential *resolution* (IRSA web-identity exchange, IMDS, STS) may perform I/O on the first call and
on refresh, and botocore caches the resolved credentials on the client.

**Alternatives considered**:
- `aioboto3` / `aiobotocore` — adds a dependency tree for an operation that does no I/O of its own;
  credential resolution is already cached. Rejected (YAGNI).
- Hand-rolled SigV4 signing — reimplements a security-critical primitive. Rejected.

**Consequence**: `boto3>=1.40,<2` is added as a runtime dependency. The async path wraps the call in
`asyncio.to_thread` so that the possible credential-refresh I/O never blocks the event loop
(constitution principle I, FR-010).

## Decision 2 — token lifetime and caching

**Decision**: Generate a fresh token per new connection; no caching layer.

**Rationale**: An RDS IAM token is valid for 15 minutes and is only validated at connect time, so
pooled connections keep working past expiry — the same property the `azure_entra` path documents,
which is why token TTL is independent of `DB_POOL_RECYCLE_SECONDS`. Generation is local signing, so
per-connection cost is negligible and a cache would add invalidation logic for no benefit (KISS).

## Decision 3 — TLS

**Decision**: `ssl="require"` (asyncpg) and `sslmode="require"` (psycopg2).

**Rationale**: RDS refuses IAM authentication over an unencrypted connection, so TLS must be
requested explicitly rather than relying on psycopg2's `prefer` default, which would silently fall
back to plaintext. This mirrors the existing `azure_entra` implementation exactly.

**Alternative considered**: `verify-full` with the RDS global CA bundle baked into the image. It is
stronger, but it diverges from `azure_entra`, enlarges the image, and adds a certificate-path
setting. Deferred as a cross-cutting decision for all cloud modes (see spec Out of Scope).

## Decision 4 — region resolution

**Decision**: `DB_AWS_REGION` when set, otherwise the `AWS_REGION` environment variable, otherwise
`boto3.session.Session().region_name`; raise a `ValueError` naming `DB_AWS_REGION` when none
resolves. `AWS_REGION` must be read directly because botocore's own `region` chain honours only
`AWS_DEFAULT_REGION` and the shared config file — verified against botocore 1.43.95 — while
EKS/ECS/Lambda export `AWS_REGION`.

**Rationale**: `AWS_REGION` is present by default on EKS/ECS/EC2, so requiring an extra variable
would be redundant configuration in the common case. But a token signed for the wrong region fails
authentication with an opaque error, and an unset region makes `generate_db_auth_token` fail deep
inside the connection attempt. Resolving (and rejecting) the region while the engine is being built
turns a runtime connection failure into a startup failure (FR-006, SC-004).

**Note**: `DB_AWS_REGION` cannot be a Pydantic-level requirement because its fallback is only known
at runtime, so this validation lives in the token provider, not in `DatabaseSettings`.

## Decision 5 — user setting

**Decision**: A dedicated `DB_AWS_IAM_USER` rather than reusing `DB_USER`.

**Rationale**: Symmetry with `DB_GCP_IAM_USER` and `DB_AZURE_ENTRA_USER` keeps
`validate_auth_requirements` uniform (every non-URL mode has a required field that fails fast) and
keeps the IAM-mapped role visibly distinct from the static-password role in configuration.

## Decision 6 — sync engine shape

**Decision**: Passwordless psycopg2 URL + a `do_connect` listener injecting the token, reusing the
`azure_entra` structure; the URL builder is factored into one passwordless helper shared by both.

**Rationale**: The `azure_entra` sync path already solves precisely this problem (per-connection
token as password, TLS required, provider closed on `engine_disposed`). Copying its shape and
extracting the shared URL construction satisfies DRY without inventing a second pattern. Unlike
`gcp_iam`, AWS needs no connector library, so plain psycopg2 is used — `DbDriver.SYNC` applies.

## Decision 7 — provider construction signature

**Decision**: The provider takes the `DatabaseSettings` object, not `(host, port, user, region)`.

**Rationale**: The project caps parameters at 2–3 and prefers a model for more; host, port, user and
region would be four. `sync_engine` already threads `DatabaseSettings` through its builders.

## Reference

The `gcp_iam` / `azure_entra` implementation in `src/lib/db/{credentials,engine,sync_engine,url}.py`
(feature 022) is the template this feature follows.
