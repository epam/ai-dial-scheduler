# Quickstart: Validating Worker Health Probes

Prerequisites: local Postgres running (e.g. via `docker compose up postgres`), `.env` configured
per `README.md`, dependencies installed (`uv sync`), migrations applied (`uv run alembic upgrade head`).

## 1. Start the worker

```sh
SERVICE=worker WORKER_HEALTH_DIR=/tmp uv run python entrypoint_worker.py
```

Expect log line `Starting worker.` followed by the scheduler starting.

## 2. Check startup + liveness marker files

```sh
ls -l /tmp/started /tmp/heartbeat
```

Expected: both files exist immediately after startup. `heartbeat`'s mtime updates every
`noop_interval_seconds` (default 60s) as the `worker_heartbeat` job ticks.

```sh
# equivalent of the liveness exec probe
[ $(( $(date +%s) - $(stat -c %Y /tmp/heartbeat) )) -lt 180 ] && echo alive || echo unhealthy
```

## 3. Check readiness marker file

```sh
test -f /tmp/ready && echo ready || echo not_ready
```

Expected: `ready` once the scheduler has started and Postgres is reachable (within one
`worker_readiness_check_interval_seconds`, default 15s).

## 4. Simulate a hung worker (liveness failure)

With the worker running, pause the process (e.g. `kill -STOP <pid>` on Linux) so the heartbeat
job stops ticking, wait past `worker_liveness_staleness_seconds` (default 180s), then re-run the
liveness check above — expect `unhealthy`. Resume the process (`kill -CONT <pid>`) and confirm
the next heartbeat tick brings it back to `alive`.

## 5. Simulate a database outage (readiness failure, liveness unaffected)

```sh
docker compose stop postgres
sleep 20  # allow one readiness poll cycle
test -f /tmp/ready && echo ready || echo not_ready   # expect not_ready
[ $(( $(date +%s) - $(stat -c %Y /tmp/heartbeat) )) -lt 180 ] && echo alive || echo unhealthy  # expect alive — unaffected by DB outage
docker compose start postgres
sleep 20
test -f /tmp/ready && echo ready || echo not_ready   # expect ready again
```

## 6. Simulate graceful shutdown

```sh
kill -TERM <worker-pid>
test -f /tmp/ready && echo ready || echo not_ready   # expect not_ready immediately, before the process exits
```

## 7. Automated verification

- Unit tests: `uv run pytest tests/unit/worker/test_health.py tests/unit/lib/services/test_health_check.py`
- Full suite + coverage: `uv run pytest --cov=src`

See [contracts/worker-health-probes.md](contracts/worker-health-probes.md) for the full probe
contract and recommended Kubernetes `exec` probe YAML.
