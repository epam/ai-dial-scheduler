# Contract: Worker Health Probes (exec-based)

The worker exposes no HTTP/TCP/gRPC listener for health. Instead it maintains three marker files
under `worker_health_dir` (default `/tmp`), checked by Kubernetes `exec` probes running a shell
command inside the container. This matches the
[Kubernetes docs' recommended pattern](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
for containers with no exposed port.

| File                      | Path (default)     | Written by                                                        |
|---------------------------|---------------------|----------------------------------------------------------------------|
| Started marker            | `/tmp/started`      | Once, after scheduler start + heartbeat job registration              |
| Heartbeat marker          | `/tmp/heartbeat`    | Once at startup, then every successful `worker_heartbeat` job tick    |
| Ready marker              | `/tmp/ready`        | Readiness poller, each cycle DB connectivity succeeds; removed on DB failure or on SIGTERM/SIGINT |

## Startup Probe

Gate: has the worker completed its one-time initialization?

```yaml
startupProbe:
  exec:
    command: ["/bin/sh", "-c", "test -f /tmp/started"]
  periodSeconds: 10
  failureThreshold: 30   # up to 5 minutes to complete startup before being killed
```

- **Exit 0** once `/tmp/started` exists.
- **Exit 1** (non-zero) until then.

While this probe has not yet succeeded once, Kubernetes suppresses the liveness and readiness
probes below (standard Kubernetes `startupProbe` semantics) — this is what gives a slow-starting
worker (e.g., loading many persisted schedules) room to finish without being killed (spec User
Story 3, FR-006).

## Liveness Probe

Gate: is the worker's scheduling loop still making forward progress? **Independent of database
state** (FR-007) — a DB outage alone must never fail this probe.

```yaml
livenessProbe:
  exec:
    command:
      - /bin/sh
      - -c
      - '[ $(( $(date +%s) - $(stat -c %Y /tmp/heartbeat) )) -lt 180 ]'
  periodSeconds: 30
  timeoutSeconds: 5
  failureThreshold: 3
```

- **Exit 0** while `/tmp/heartbeat`'s mtime age is below `worker_liveness_staleness_seconds`
  (default `180` = `3 * noop_interval_seconds`; adjust the `180` literal in the command if the
  setting is overridden for an environment).
- **Exit 1** once the heartbeat has gone stale — Kubernetes restarts the pod after
  `failureThreshold` consecutive failures.

## Readiness Probe

Gate: has the worker finished starting up and is the database currently reachable?

```yaml
readinessProbe:
  exec:
    command: ["/bin/sh", "-c", "test -f /tmp/ready"]
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 2
```

- **Exit 0** while `/tmp/ready` exists (worker started, last DB check succeeded, not shutting
  down).
- **Exit 1** while starting up, while the database is unreachable, or once shutdown begins
  (SIGTERM/SIGINT immediately removes the file, before the scheduler shuts down).

## Notes for the external Helm chart/deploy repository

- All three commands assume a POSIX shell and BusyBox `stat`/`test`/`date`, present in the
  existing `python:3.14-alpine`-based worker image without additional packages (see
  `research.md` §7).
- Keep `worker_health_dir` (default `/tmp`) as an `emptyDir`-backed or ephemeral path — it is
  process-local scratch state, not meant to persist across pod restarts.
- If `worker_liveness_staleness_seconds` or `noop_interval_seconds` are overridden via
  environment variables for a given deployment, update the literal `180` in the liveness
  command accordingly (the exec command cannot read application settings directly).
