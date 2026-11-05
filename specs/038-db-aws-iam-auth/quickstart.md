# Quickstart: `aws_iam` Database Authentication

## 1. Prepare the database

Enable IAM database authentication on the RDS/Aurora PostgreSQL instance, then create the role and
grant it `rds_iam`:

```sql
CREATE ROLE scheduler_iam WITH LOGIN;
GRANT rds_iam TO scheduler_iam;
GRANT ALL PRIVILEGES ON DATABASE scheduler TO scheduler_iam;
```

The role also needs privileges on the schema used by the app (`DB_SCHEMA`, `public` by default),
including on `apscheduler_jobs`, which APScheduler creates itself.

## 2. Grant the workload permission to connect

Attach a policy to the workload's IAM role (IRSA on EKS, task role on ECS, instance profile on EC2):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "rds-db:connect",
    "Resource": "arn:aws:rds-db:eu-central-1:123456789012:dbuser:db-ABCDEFGHIJKLMNOP/scheduler_iam"
  }]
}
```

`db-ABCDEFGHIJKLMNOP` is the instance's **resource ID** (not its name) — read it from
`aws rds describe-db-instances --query 'DBInstances[].DbiResourceId'`.

## 3. Configure the service

```dotenv
DB_AUTH_MODE=aws_iam
DB_HOST=scheduler-pg.abcdefghijkl.eu-central-1.rds.amazonaws.com
DB_NAME=scheduler
DB_AWS_IAM_USER=scheduler_iam
# DB_AWS_REGION=eu-central-1   # only if the environment does not provide a region
```

Do **not** set `DB_PASSWORD` or `DB_URL` — startup rejects them in this mode.

## 4. Run migrations, then the services

```bash
SERVICE=migrations  # alembic upgrade head, online path
SERVICE=api
SERVICE=worker
```

Offline migrations (`alembic upgrade head --sql`) are not supported in this mode.

## 5. Verify

- `GET /health` returns 200 and `GET /ready` reports the database reachable.
- The worker's `ready` marker file appears in `WORKER_HEALTH_DIR`.
- Logs contain `Initializing a new DB engine with aws_iam auth mode.` and no token material.
- Create a schedule through the API and confirm the worker picks it up — this exercises the async
  engine (API + worker) and the sync job-store engine together.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `DB auth mode 'aws_iam' requires: db_aws_iam_user.` | `DB_AWS_IAM_USER` not set. |
| `DB auth mode 'aws_iam' must not set: db_password.` | Remove the leftover password. |
| `AWS region could not be resolved from the environment; set DB_AWS_REGION.` | No ambient region — set it explicitly. |
| `PAM authentication failed` | Role missing `rds_iam`, or the IAM policy's dbuser/resource ID is wrong. |
| `no pg_hba.conf entry ... SSL off` | TLS was disabled outside the app; IAM auth requires TLS. |
